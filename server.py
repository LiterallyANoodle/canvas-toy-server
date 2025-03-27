# Python 3 server example
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from base64 import b64decode
from io import BytesIO
import datetime
from pathlib import Path
import json

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

        # check source/prevent spam 

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
            self.save_image(img, self.client_address[0])
            response_body += "Successfully saved image! \n"
        except:
            response_body += "Failed to save image. \n"

        # send on discord webhook 

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
    
    def save_image(self, img, sender_ip) -> None:

        # remove transparency 
        if img.mode in ('RGBA', 'LA'):
            alpha = img.getchannel('A')
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=alpha)
            img = background

        # make unique name and save
        timestamp = datetime.datetime.now()
        fp = Path(config['saved_images_path'])
        img.save((fp / f'{str(timestamp).replace(':', '.')} {sender_ip}.png'), 'PNG')

if __name__ == "__main__":
    config = ServerConfiguration().configuration
    web_server = HTTPServer((config['host_name'], config['server_port']), CanvasToyServer)
    print(f"Server started http://{config['host_name']}:{config['server_port']}")

    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        print("Received interrupt command.")
        pass

    web_server.server_close()
    print("Server stopped.")