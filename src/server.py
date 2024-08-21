import json
import requests
import time
import urllib3
import os
from infisical_client import *
from prometheus_client import start_http_server, Gauge, Enum, Counter
import time


urllib3.disable_warnings()


infisical_settings = {
    "domain": os.environ["INFISICAL_URL"],
    "env": os.environ["INFISICAL_ENV"],
    "project": os.environ["INFISICAL_PROJECT_ID"],
    "client_id": os.environ["INFISICAL_CLIENT_ID"],
    "client_secret": os.environ["INFISICAL_CLIENT_SECRET"]
}


class MeteoFranceClient(object):
    infisical_secrets = ""

    def __init__(self, infisical_secrets):
        self.infisical_secrets = infisical_secrets
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})

    def request(self, method, url, **kwargs):
        # First request will always need to obtain a token first
        if 'Authorization' not in self.session.headers:
            self.obtain_token()

        # Optimistically attempt to dispatch reqest
        response = self.session.request(method, url, **kwargs)
        if self.token_has_expired(response):
            # We got an 'Access token expired' response => refresh token
            self.obtain_token()
            # Re-dispatch the request that previously failed
            response = self.session.request(method, url, **kwargs)

        return response

    def token_has_expired(self, response):
        status = response.status_code
        content_type = response.headers['Content-Type']
        if status == 401 and 'application/json' in content_type:
            repJson = response.text
            if 'Invalid JWT token' in repJson['description']:
                return True
        return False

    def obtain_token(self):
        # Obtain new token
        data = {'grant_type': 'client_credentials'}
        headers = {'Authorization': 'Basic ' +
                   self.infisical_secrets["APPLICATION_ID"]}
        access_token_response = requests.post(
            self.infisical_secrets["TOKEN_URL"], data=data, verify=False, allow_redirects=False, headers=headers)
        token = access_token_response.json()['access_token']
        self.session.headers.update({'Authorization': 'Bearer %s' % token})


class MeteoMetrics:
    meteo_client = ""
    config = ""

    def __init__(self, polling_interval_seconds, meteo_client, config):
        self.polling_interval_seconds = polling_interval_seconds
        self.config = config

        self.meteo_client = meteo_client

        self.meteo_temperature = Gauge(
            "meteo_temperature", "Temperature sensor", ['station_id'])
        self.meteo_humidity = Gauge(
            "meteo_humidity", "Humidity sensor", ['station_id'])
        self.meteo_rain = Gauge(
            "meteo_rain", "Rain sensor", ['station_id'])
        self.meteo_wind = Gauge(
            "meteo_wind", "Wind sensor", ['station_id'])

    def run_metrics_loop(self):
        """Metrics fetching loop"""

        while True:
            print(str(time.strftime("%Y-%m-%d %H:%M:%S")) +
                  " -- GATHERING DATA")
            self.fetch()
            time.sleep(self.polling_interval_seconds)

    def fetch(self):
        try:
            response = self.meteo_client.request(
                'GET', 'https://public-api.meteofrance.fr/public/DPObs/v1/station/infrahoraire-6m?id_station='+self.config["STATION_ID"]+'&format=json', verify=False)

            station_data = {
                'temp': round(response.json()[0]["t"]-275.15, 2),
                'humidity': round(response.json()[0]["u"], 2),
                'rain': round(response.json()[0]["rr_per"], 2),
                'wind': round(response.json()[0]["ff"], 2)
            }
        except Exception as e:
            print("Error", e)
            exit(1)

        self.meteo_temperature.labels(
            station_id=self.config["STATION_ID"]).set(station_data['temp'])
        self.meteo_humidity.labels(
            station_id=self.config["STATION_ID"]).set(station_data['humidity'])
        self.meteo_rain.labels(
            station_id=self.config["STATION_ID"]).set(station_data['rain'])
        self.meteo_wind.labels(
            station_id=self.config["STATION_ID"]).set(station_data['wind'])


def main():
    """Main entry point"""

    polling_interval_seconds = int(
        os.getenv("POLLING_INTERVAL_SECONDS", "30"))
    exporter_port = int(os.getenv("EXPORTER_PORT", "8000"))

    infisical_client = InfisicalClient(ClientSettings(
        auth=AuthenticationOptions(
            universal_auth=UniversalAuthMethod(
                client_id=infisical_settings['client_id'],
                client_secret=infisical_settings['client_secret']
            )
        ),
        site_url=infisical_settings['domain']
    ))

    infisical_secret_list = infisical_client.listSecrets(options=ListSecretsOptions(
        environment=infisical_settings['env'],
        project_id=infisical_settings['project'],
        path="/Supervision/Meteofrance"
    ))
    infisical_secrets = {}
    for secret in infisical_secret_list:
        infisical_secrets[secret.secret_key] = secret.secret_value

    meteo_client = MeteoFranceClient(infisical_secrets)

    app_metrics = MeteoMetrics(
        polling_interval_seconds, meteo_client, infisical_secrets
    )
    start_http_server(exporter_port)
    app_metrics.run_metrics_loop()


if __name__ == "__main__":
    main()
