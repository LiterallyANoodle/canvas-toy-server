# Python 3 server example
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from base64 import b64decode
from io import BytesIO
import datetime
from pathlib import Path

# server params
hostName = "localhost"
serverPort = 6969

# image params
max_height = 500
max_width = 500
allowed_image_type = "PNG"
Image.MAX_IMAGE_PIXELS = (max_height * max_width) # change pillow setting to prevent zip bomb style attack

class CanvasToyServer(BaseHTTPRequestHandler):
    
    def do_POST(self) -> None:

        # check source/prevent spam 

        # read incoming body
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)

        # verify content 
        img = self.validate_image(body)

        # save to file 
        self.save_image(img, self.client_address[0])

        # send on discord webhook 

        # write headers based on status
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

    def validate_image(self, body) -> Image:
        
        try:
            # convert from base64
            body_str = body.decode("utf-8")
            (body_prefix, body_suffix) = body_str.split(',')
            assert body_prefix == 'data:image/png;base64'
            img_bytes = BytesIO(b64decode(body_suffix))

            # load in pillow and verify contents
            try:
                img = Image.open(img_bytes)
                img.verify()
                print("Valid image.")
                return Image.open(img_bytes)
            except:
                print("Invalid image.")

        except:
            print("Invalid body.")

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
        fp = Path('./saved_images')
        img.save((fp / f'{str(timestamp).replace(':', '.')} {sender_ip}.png'), 'PNG')

if __name__ == "__main__":        
    webServer = HTTPServer((hostName, serverPort), CanvasToyServer)
    print(f"Server started http://{hostName}:{serverPort}")

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        print("Received interrupt command.")
        pass

    webServer.server_close()
    print("Server stopped.")