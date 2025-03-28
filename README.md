# canvas-toy-server

Using python 3.12

Recommend using a virtual environment.

Install packages with `pip install requirements.txt`.

## Configuration
```json
{
    "host_name": "localhost", // domain of the server
    "server_port": 6969, // port the server will serve on
    "max_height": 500, // submitted image height
    "max_width": 500, // submitted image width
    "allowed_image_type": "PNG", // allowed image types (Note: not tested with anything other than PNG)
    "saved_images_path": "./saved_images", // path to save received images on
    "webhook_path": "", // path following discord.com on the webhook URL. starting at the /
    "global_rate_period": 3600, // how long to wait in seconds before removing old request timestamps from active history
    "global_rate_limit": 100 // how many requests can be received in a period before being rate limited 
}
```