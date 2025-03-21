# Python 3 server example
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from base64 import b64decode
from io import BytesIO

# server params
hostName = "localhost"
serverPort = 6969

# image params
max_height = 500
max_width = 500
allowed_image_type = "PNG"
Image.MAX_IMAGE_PIXELS = (max_height * max_width) # change pillow setting to prevent zip bomb style attack

class MyServer(BaseHTTPRequestHandler):
    
    def do_POST(self) -> None:

        # check source/prevent spam 

        # write headers
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        # read incoming body
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)

        # verify content 
        img = self.validate_image(body)

        # save to file 

        # send on discord webhook 

    def validate_image(self, body) -> Image:
        
        try:
            print(type(body))
            # convert from base64
            body_str = body.decode("utf-8")
            body_decoded = BytesIO(b64decode(body_str))
            print(type(body_decoded))

            # load in pillow and verify contents
            print(Image.MAX_IMAGE_PIXELS)
            img = Image.open(body_decoded)
            try:
                img.verify()
                print("Valid image")
                return img
            except:
                print("Invalid image")

        except:
            print("Invalid body.")

        return None

if __name__ == "__main__":        
    webServer = HTTPServer((hostName, serverPort), MyServer)
    print(f"Server started http://{hostName}:{serverPort}")

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        print("Received interrupt command.")
        pass

    webServer.server_close()
    print("Server stopped.")