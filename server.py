# Python 3 server example
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from base64 import b64decode
from io import BytesIO
import datetime
from time import time
from pathlib import Path
import json
from uuid import uuid4
import http.client

class ServerConfiguration:

    configuration = {}

    def __init__(self) -> None:
        try: 
            config_file = open("configuration.json")
            self.configuration = json.loads(config_file.read())
            Image.MAX_IMAGE_PIXELS = (self.configuration['max_height'] * self.configuration['max_width']) # change pillow setting to prevent zip bomb style attack
        except:
            print("Error: Unable to load server configuration.")
            exit()

class CanvasToyServer(BaseHTTPRequestHandler):

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def do_POST(self) -> None:

        timestamp = datetime.datetime.now()
        time_sec = time()
        # prevent spam with global rate limit
        global request_history
        request_history.append(time_sec)
        request_history = [t for t in request_history if (t + config['global_rate_period']) > time_sec] # remove expired request history
        if len(request_history) > config['global_rate_limit']:
            print("Request dropped: rate limit exceeded.")
            self.send_post_response(503, "Receiving too many requests! Please wait a while.\n")
            return

        # read incoming body
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)

        response_body = ''

        # verify content 
        try: 
            img = self.validate_image(body)
        except: 
            self.send_post_response(400, "Invalid message body.\n")
            return

        # save to file 
        try:
            img = self.save_image(img, self.client_address[0], timestamp)
            response_body += "Successfully saved image!\n"
        except:
            response_body += "Failed to save image.\n"

        # send on discord webhook 
        wh_status = self.send_image_on_discord_webhook(config['webhook_path'], img, timestamp)
        if wh_status == 200:
            response_body += "Successfully sent to discord!\n"
        else:
            response_body += "Failed to send to discord.\n"

        self.send_post_response(200, response_body)

    def send_post_response(self, status, response_body):
        # write headers based on status
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes(response_body, 'utf8'))

    def validate_image(self, body) -> Image:
        
        try:
            # convert from base64
            body_str = body.decode("utf-8")
            (body_prefix, body_suffix) = body_str.split(',')
            assert body_prefix == 'data:image/png;base64'
            img_bytes = BytesIO(b64decode(body_suffix))

            # load in pillow and verify contents
            img = Image.open(img_bytes)
            img.verify()
            print("Valid image.")
            return Image.open(img_bytes)

        except:
            print(f"{datetime.datetime.now()} Invalid body from {self.client_address[0]}")
            raise ValueError
    
    def save_image(self, img, sender_ip, timestamp) -> Image:

        # remove transparency 
        if img.mode in ('RGBA', 'LA'):
            alpha = img.getchannel('A')
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=alpha)
            img = background

        # make unique name and save
        fp = Path(config['saved_images_path'])
        img.save((fp / f'{str(timestamp).replace(':', '.')}.png'), 'PNG')
        return img

    def send_image_on_discord_webhook(self, webhook_path, img, timestamp) -> int:

        boundary = f'------Boundary{uuid4().hex}'

        payload_json = json.dumps({"content":f"{timestamp}"})

        message_body = \
        f'--{boundary}\r\n' + \
        f'Content-Disposition: form-data; name="payload_json"\r\n' + \
        f'Content-Type: application/json\r\n\r\n' + \
        f'{payload_json}\r\n' + \
        f'--{boundary}\r\n' + \
        f'Content-Disposition: form-data; name="file"; filename="{str(timestamp).replace(":", ".")}.png"\r\n' + \
        f'Content-Type: application/octet-stream\r\n\r\n'

        img_bytes = BytesIO()
        img.save(img_bytes, format='PNG')

        message_body = message_body.encode() + img_bytes.getvalue() + f'\r\n--{boundary}--\r\n'.encode()

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(message_body))
        }

        conn = http.client.HTTPSConnection('discord.com')
        conn.request(
            'POST',
            f'{webhook_path}',
            body=message_body,
            headers=headers
        )

        response = conn.getresponse()
        print(f"Status: {response.status}")
        print("Response:", response.read().decode())
        conn.close()

        return response.status

if __name__ == "__main__":
    config = ServerConfiguration().configuration
    request_history = []
    web_server = HTTPServer((config['host_name'], config['server_port']), CanvasToyServer)
    print(f"Server started http://{config['host_name']}:{config['server_port']}")

    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        print("Received interrupt command.")
        pass

    web_server.server_close()
    print("Server stopped.")