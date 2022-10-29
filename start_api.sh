#!/bin/bash
rasa run -m models --enable-api --cors "*" --debug --credentials credentials.yml --endpoints endpoints.yml