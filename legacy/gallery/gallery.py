from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import psycopg2
import signal
from uuid import uuid4
from urllib.parse import urlparse
from datetime import datetime

class GalleryConfiguration:

    configuration = {}

    def __init__(self) -> None:
        try: 
            config_file = open("configuration.json")
            self.configuration = json.loads(config_file.read())
        except:
            print("Error: Unable to load server configuration.")
            exit()

class GalleryServer(BaseHTTPRequestHandler):

    def do_OPTIONS(self) -> None:

        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:

        response_body = {}

        # read URL path for image number
        # of the form /dragon-gallery/image/{number}
        # else redirect to 404
        try:
            split_path = self.path.split('/')
            assert split_path[0] == ''
            assert split_path[1] == 'dragon-gallery'
            assert split_path[2] == 'image'
            img_index = split_path[3]
        except:
            print('Request failed with invalid URL path.')
            response_body['status'] = '404'
            response_body['message'] = 'Gallery index does not exist.'
            self.send_get_response(404, response_body, [ lambda: self.send_header("Location", "http://noodledragon.fans/404") ])
            return

        # find image in database or the 404 image
        try:
            print(f"Connecting to database {config['db_name']} on {config['db_host_name']}")
            conn = psycopg2.connect(
                dbname=config['db_name'],
                user=config['db_user'],
                password=config['db_pwd'],
                host=config['db_host_name']
            )
            cur = conn.cursor()
            cur.execute(f'SELECT timestamp, img_filename FROM "Image" WHERE gallery_number = {img_index}')
            image_tup = cur.fetchone()
            if image_tup is not None:
                timestamp = image_tup[0]
                img_filename = image_tup[1]
            else:
                response_body['message'] = 'Failed to retrieve row from database.'
                self.send_get_response(400, response_body, [])
                return
            print('Row retrieved: ' + str(image_tup))

            cur.close()
        
        except (Exception, psycopg2.DatabaseError) as error:
            print(error)
        finally:
            if conn is not None:
                conn.close()
                print('Database conneciton closed.')

        # send back image in response
        response_body['message'] = 'Successfully found image.'
        response_body['timestamp'] = str(timestamp)
        response_body['image'] = str(img_filename)
        self.send_get_response(200, json.dumps(response_body), [])

    def send_get_response(self, status, response_body, custom_headers):
        # write headers based on status
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-type", "text/html")
        for h in custom_headers:
            h()
        self.end_headers()
        self.wfile.write(bytes(str(response_body), 'utf8'))

def sigterm_handler(_signo, _stackframe):
    exit(0)

if __name__ == "__main__":
    config = GalleryConfiguration().configuration
    web_server = HTTPServer((config['host_name'], config['server_port']), GalleryServer)
    print(f"Server started http://{config['host_name']}:{config['server_port']}")
    signal.signal(signal.SIGTERM, sigterm_handler)

    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        print("Received interrupt command.")

    web_server.server_close()
    print("Server stopped.")