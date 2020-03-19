FROM blueztestbot/bluez-build:latest

COPY *.sh /

CMD [ "/entrypoint.sh" ]
