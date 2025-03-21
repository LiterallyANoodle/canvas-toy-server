# Python 3 server example
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from base64 import b64decode

# server params
hostName = "localhost"
serverPort = 6969

# image params
max_height = 500
max_width = 500
allowed_image_type = "PNG"

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
        self.validate_image(body)

        # save to file 

        # send on discord webhook 

    def validate_image(self, body) -> Image:
        
        try:
            print(type(body))
            # convert from base64
            body_decoded = b64decode(str(body))
            print(type(body_decoded))
            self.wfile.write(body_decoded)

            # check for zip bomb 

            # load in pillow and verify contents
        except:
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