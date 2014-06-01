#!/usr/bin/env python
import os
import random
from datetime import datetime, timedelta
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


# Some constants
SMS_CODE_RESET = timedelta(minutes=30)
SMS_CODE_GRACE = timedelta(minutes=5)
USER_CHECKIN_EXPIRE = timedelta(minutes=15)


"""Collection schemas

users: {
    phone_number: string
    last_checkin: datetime
}

codes: {
    code: string
    created: datetime
}

posts: {
    message: string
    poster: phone_number
    submitted: datetime
    showtime: datetime  # not present if it hasn't been shown yet
    extenders: [user_ids]
}
"""


class NoUserException(Exception):
    """When user uses code that doesn't exist"""

class NotCheckedInException(Exception):
    """When user tries to vote or post before checking in"""


class ChillOut(Exception):
    """When users get too excited (try to re-up-vote or re-post too soon)."""


def notz(dt):
    """Remove the timezone info from a datetime object"""
    return dt.replace(tzinfo=None)


def create_sms_code():
    """Create a new code. More races woo!"""
    while True:
        code = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz1234567890') for _ in range(6))
        existing_with_code = pymongo.db.codes.find_one({'code': code})
        if existing_with_code is None:
            break

    new_sms = {
        'code': code,
        'created': datetime.now()
    }
    pymongo.db.codes.insert(new_sms)
    return new_sms


def get_sms_code():
    """Fetches the most up-to-date SMS code for the billboard.

    This may trigger a new code if one is due.
    """
    codes = pymongo.db.codes.find().sort('created')
    try:
        current = next(codes)
    except StopIteration:
        current = create_sms_code()
    if notz(current['created']) + SMS_CODE_RESET > datetime.now():
        # yo, WARNING: off to the races!
        current = create_sms_code()
    return current['code']


def check_sms_code(test_code):
    """Checks whether the SMS code is currently valid."""
    codes = pymongo.db.codes.find().sort('created')
    current = next(codes)
    if test_code == current['code']:
        return True
    else:
        previous = next(codes)
        if (test_code == previous['code'] and
            datetime.now() - notz(current['created']) < SMS_CODE_GRACE):
            return True
        else:
            return False


def get_queue():
    """Fetch all posts currently queued."""
    unshown = pymongo.db.posts.find({'showtime': {'$exists': False}})
    queue_in_order = unshown.sort('submitted')
    return queue_in_order


def is_checked_in(phone_number):
    """Test whether a user is checked in or not."""
    user = pymongo.db.users.find_one({'phone_number': phone_number})
    if user is None:
        return False
    a_ok = notz(user['last_checkin']) + USER_CHECKIN_EXPIRE > datetime.now()
    return a_ok


def check_in(phone_number, code):
    """Check in (and possibly create) a user, verified by the active code.

    Returns the user's data, or None if the code is wrong or expired.

    The correct code is currently hard-coded to ABC.
    """

    if code != 'ABC':
        raise NoUserException("You fucked up")

    user = pymongo.db.users.find_one({'phone_number': phone_number})
    return user


def has_checked_in(phone_number):
    """ Checks if the phone number is already in the database
    If it is, then the user is connected and returns true. 
    Otherwise returns false.

    Currently returns a random boolean"""
    import random
    return bool(random.getrandbits(1))


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


def save_vote(phone_number):
    """Register a vote for a user.

    Returns 1 if the vote was counted.
    Raises ChillOut if the user has already voted for the showing post.

    Currently it is hard-coded to always succeed
    """
    return 1


@app.route('/sms', methods=['GET','POST'])
def send_sms():

    #Get number and response
    from_number = request.values.get('From', None)
    from_response = request.values.get('Body',None)
    first_word = from_response.lower().split(' ',1)[0];
    resp = twilio.twiml.Response()
    #Checks if user already checked in
    if has_checked_in(from_number):
         #Check if user response is vote
        if "vote" in first_word:
            if vote():
                message="Vote successful"
            else:
                message="Vote unsuccessful"

        #Check if user response is a post
        elif "post" in first_word:
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

    #User hasn't checked in but is checking in now
    elif "post" not in first_word and "vote" not in first_word:
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
    else:
        #error handling
        message="Not checked in"
        resp.message(message)
        return str(resp)


    resp.message(message)

    return str(resp)


# dev stuff

def push():
    """Push a test request context"""
    ctx = app.test_request_context()
    ctx.push()
    return ctx


if __name__ == '__main__':
    app.run(debug=True)
