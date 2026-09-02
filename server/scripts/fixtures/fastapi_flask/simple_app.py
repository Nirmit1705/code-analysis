from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    pass


@app.route("/legacy", methods=["GET", "POST"])
def legacy():
    pass
