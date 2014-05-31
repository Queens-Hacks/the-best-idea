#!/usr/bin/env python
import os
import twilio.twiml
from flask import Flask, request
from flask.ext.pymongo import PyMongo

app = Flask(__name__)


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


class NoUserException(Exception):
    """When user uses code that doesn't exist"""


class ChillOut(Exception):
    """When users get too excited (try to re-up-vote or re-post too soon)."""


def check_in(phone_number, code):
    """Check in (and possibly create) a user, verified by the active code.

    Returns the user's data, or None if the code is wrong.

    The correct code is currently hard-coded to ABC.
    """
    user_data = {
        'phone_number': phone_number
    }

    if code != 'ABC':
        raise NoUserException("You fucked up")

    return user_data


def vote():
    """ Allows users to vote for the current posted message and returns
    True if vote was registered, otherwise returns False. 

    Currently defaults to True"""
    return True


def post_message(phone_number, message):
    """Try to queue a message for a user.

    Returns the message's position in the queue.

    Raises ChillOut if the user has posted too many messages recently.

    Currently hard-coded in a state where:
    posting any message succeeds and returns a random queue position
    EXCEPT the message 'fail' raises ChillOut.
    """
    import random
    if message == 'fail':
        raise ChillOut('Whoa. Chill out, hey. So many messages.')
    return random.randint(1, 6)


@app.route('/sms', methods=['GET','POST'])
def send_sms():    
    
    #Get number and response
    from_number = request.values.get('From', None)
    from_response = request.values.get('Body',None)
    
    resp = twilio.twiml.Response()
    
    #Check if user response is vote
    if "vote" in from_response.lower().split(' ',1)[0]:
        if vote():
            message="Vote successful"
        else:
            message="Vote unsuccessful"


    #Check if user response is a post
    elif "post" in from_response.lower().split(' ',1)[0]:
        queue_num = post_message(from_number,from_response.lower().split(' ',1)[1])
        message = "Your message is queued in position " + str(queue_num)

    else:
        #check if user exists
        try:
            check = check_in(from_number,from_response);

        except NoUserException:
            #error handling
            message="fucked up"
            resp.message(message)
            return str(resp)

        message = ''' Thanks for checking in! To vote, Please
        text 'vote', otherwise text 'post' and type in your message '''
   
    resp.message(message)

    return str(resp)


if __name__ == '__main__':
    app.run(debug=True)
