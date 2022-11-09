# This files contains your custom actions which can be used to run
# custom Python code.
#
# See this guide on how to implement these action:
# https://rasa.com/docs/rasa/custom-actions
from typing import Dict, Text, Any, List, Optional
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
import json
from sanic.request import Request
import asyncio
import logging
import inspect
from sanic import Blueprint, response
from sanic.response import HTTPResponse
from asyncio import Queue, CancelledError
from typing import Text, Dict, Any, Optional, Callable, Awaitable, NoReturn
import rasa.utils.endpoints
from rasa.core.channels.channel import (
    InputChannel,
    CollectingOutputChannel,
    UserMessage,
)
from rasa_sdk.events import AllSlotsReset,FollowupAction
import requests

logger = logging.getLogger(__name__)
backendServerUrl = "http://127.0.0.1:8000"

# ******************************************************************************************************************************************
# Helper functions **************************************************
# ******************************************************************************************************************************************

def extract_metadata_from_tracker(tracker):
    '''
    Extracts metadata from the rasa webchart
    '''
    events = tracker.current_state()['events']
    user_events = []
    for e in events:
        if e['event'] == 'user':
            user_events.append(e)

    return user_events[-1]['metadata']


# ******************************************************************************************************************************************
# Actions Sections **************************************************
# ******************************************************************************************************************************************

class ActionGetStarted(Action):
    '''
    Greets the user and introduces the bot
    '''

    def name(self) -> Text:
        return "action_get_started"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        metadata = extract_metadata_from_tracker(tracker)
        try:
            firstname = metadata['metadata']['firstname'].title()            
        except KeyError as error:
            firstname = ''           
        dispatcher.utter_message("Hi there "+firstname+" 👋")
        dispatcher.utter_message("My name is Buzz bot your USIU personal assistant.")
        dispatcher.utter_message("How may I help you?")
        return []

class ActionResetAllSlots(Action):
    '''
    Class to reset all slots before asking for any form values
    '''

    def name(self):
        return "action_reset_all_slots"

    def run(self, dispatcher, tracker, domain):
        return [AllSlotsReset()]

class ActionFetchCourseAssignments(Action):
    '''
    Send a request to the backend server and fetch assignments of a particular course
    '''

    def name(self) -> Text:
        return "action_fetch_course_assignments"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        metadata = extract_metadata_from_tracker(tracker)
        userId="ddd"
        token="dddd"
        # try:
        #     userId = metadata['metadata']['userId']  
        #     token = metadata['metadata']['token']            
        # except KeyError as error:
        #     userId = ''   
        #     token = ''     

        payload = tracker.get_slot("course_code")
        print("******************************")
        print(payload)
        print("******************************")

        if payload == "exit" or payload == "stop" or payload == "cancel" or payload == "close":
            dispatcher.utter_message(template="/stop")
            return [FollowupAction("action_reset_all_slots")]
        else:        
            if payload and len(payload)!=7:
                dispatcher.utter_message("Seem you entered a course code that does not exist. Re enter the course code & make sure its it follows the format > APT3010")
                return []
            dispatcher.utter_message("Fetching your assignments from "+payload+" ...")
            response = getAssignments(payload,userId,token)
            if(response.status_code == 201):
                dispatcher.utter_message('Below are the assignments for the this class: ')
                print("***********")
                print(response)
                print("***********")
            else:
                dispatcher.utter_message(template="/stop")                
            return [FollowupAction("action_reset_all_slots")]        

# ******************************************************************************************************************************************
# Backend API Requests Section **************************************************
# ******************************************************************************************************************************************

def getAssignments(payload,userId,token):
    '''
    Get assignments for a specific course
    '''
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer '+token
    }    
    url = backendServerUrl+'/getCourseAssignments/'+payload
    # if data and userId and token:
    response = requests.post(url, headers=headers)
    return response
    # else:
        # res = {'status_code': 500}
        # return res


# ******************************************************************************************************************************************
# RASA Input Output channel **************************************************
# ******************************************************************************************************************************************

class RasaIO(InputChannel):
    """A custom http input channel.

    This implementation is the basis for a custom implementation of a chat
    frontend. You can customize this to send messages to Rasa and
    retrieve responses from the assistant."""

    @classmethod
    def name(cls) -> Text:
        return "RasaIO"

    @staticmethod
    async def on_message_wrapper(
        on_new_message: Callable[[UserMessage], Awaitable[Any]],
        text: Text,
        queue: Queue,
        sender_id: Text,
        input_channel: Text,
        metadata: Optional[Dict[Text, Any]],
    ) -> None:
        collector = QueueOutputChannel(queue)

        message = UserMessage(
            text, collector, sender_id, input_channel=input_channel, metadata=metadata
        )
        await on_new_message(message)

        await queue.put("DONE")

    async def _extract_sender(self, req: Request) -> Optional[Text]:
        return req.json.get("sender", None)

    def _extract_message(self, req: Request) -> Optional[Text]:
        return req.json.get("message", None)

    def _extract_input_channel(self, req: Request) -> Text:
        return req.json.get("input_channel") or self.name()

    def stream_response(
        self,
        on_new_message: Callable[[UserMessage], Awaitable[None]],
        text: Text,
        sender_id: Text,
        input_channel: Text,
        metadata: Optional[Dict[Text, Any]],
    ) -> Callable[[Any], Awaitable[None]]:
        async def stream(resp: Any) -> None:
            q = Queue()
            task = asyncio.ensure_future(
                self.on_message_wrapper(
                    on_new_message, text, q, sender_id, input_channel, metadata
                )
            )
            while True:
                result = await q.get()
                if result == "DONE":
                    break
                else:
                    await resp.write(json.dumps(result) + "\n")
            await task

        return stream

    def get_metadata(self, request: Request) -> Optional[Dict[Text, Any]]:
        metadata = request.json
        return metadata

    def blueprint(
        self, on_new_message: Callable[[UserMessage], Awaitable[None]]
    ) -> Blueprint:
        custom_webhook = Blueprint(
            "custom_webhook_{}".format(type(self).__name__),
            inspect.getmodule(self).__name__,
        )

        @custom_webhook.route("/", methods=["GET"])
        async def health(request: Request) -> HTTPResponse:
            return response.json({"status": "ok"})

        @custom_webhook.route("/webhook", methods=["POST"])
        async def receive(request: Request) -> HTTPResponse:
            sender_id = await self._extract_sender(request)
            text = self._extract_message(request)
            should_use_stream = rasa.utils.endpoints.bool_arg(
                request, "stream", default=False
            )
            input_channel = self._extract_input_channel(request)
            metadata = self.get_metadata(request)

            if should_use_stream:
                return response.stream(
                    self.stream_response(
                        on_new_message, text, sender_id, input_channel, metadata
                    ),
                    content_type="text/event-stream",
                )
            else:
                collector = CollectingOutputChannel()
                try:
                    await on_new_message(
                        UserMessage(
                            text,
                            collector,
                            sender_id,
                            input_channel=input_channel,
                            metadata=metadata,
                        )
                    )
                except CancelledError:
                    logger.error(
                        f"Message handling timed out for " f"user message '{text}'."
                    )
                except Exception:
                    logger.exception(
                        f"An exception occured while handling "
                        f"user message '{text}'."
                    )
                return response.json(collector.messages)

        return custom_webhook


class QueueOutputChannel(CollectingOutputChannel):
    """Output channel that collects send messages in a list

    (doesn't send them anywhere, just collects them)."""

    @classmethod
    def name(cls) -> Text:
        return "queue"

    def __init__(self, message_queue: Optional[Queue] = None) -> None:
        super().__init__()
        self.messages = Queue() if not message_queue else message_queue

    def latest_output(self) -> NoReturn:
        raise NotImplementedError("A queue doesn't allow to peek at messages.")

    async def _persist_message(self, message) -> None:
        await self.messages.put(message)        
