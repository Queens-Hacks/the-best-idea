#!/usr/bin/env python
import os
from flask import Flask
from flask.ext.pymongo import PyMongo


CONFIGS = (
    'AUTH_TOKEN',
    'CELL_NUM',
    'MONGOLAB_URI',
    'TWILIO_NUM',
)


# Set up the app
app = Flask(__name__)
app.config.update(**{v: os.environ[v] for v in CONFIGS})
app.config['MONGO_URI'] = app.config['MONGOLAB_URI']  # for flask-pymongo


# Initialize extensions
pymongo = PyMongo(app)


@app.route('/')
def home():
    return "yo"


if __name__ == '__main__':
    app.run(debug=True)