from mangum import Mangum

from main import app

app.root_path = "/prod"
handler = Mangum(app, api_gateway_base_path="/prod", lifespan="off")
