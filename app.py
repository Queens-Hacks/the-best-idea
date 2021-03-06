#!/usr/bin/env python
import os
import random
from datetime import datetime, timedelta
import twilio.twiml
from bson import ObjectId
from flask import Flask, request, jsonify, abort, render_template
from flask.ext.pymongo import PyMongo, ASCENDING, DESCENDING
from utils import crossdomain, tznow
from bson import ObjectId
from bson.errors import InvalidId


# sockets module monkey-patches this to hook in to events
socket_push = lambda **kwargs: None


app = Flask(__name__)


CONFIGS = (
    'WEBAPP_URL',
    'AUTH_TOKEN',
    'CELL_NUM',
    'MONGOLAB_URI',
    'TWILIO_NUM',
)


# Set up the app
app = Flask(__name__)
app.config.update(DEBUG=(os.environ.get('DEBUG') == 'TRUE'))
app.config.update(**{v: os.environ[v] for v in CONFIGS})
app.config['MONGO_URI'] = app.config['MONGOLAB_URI']  # for flask-pymongo


# Initialize extensions
pymongo = PyMongo(app)


# Some constants
SMS_CODE_RESET = timedelta(minutes=10)
SMS_CODE_GRACE = timedelta(minutes=10)
USER_CHECKIN_EXPIRE = timedelta(minutes=15)
USER_POST_THROTTLE = timedelta(seconds=0)


"""Collection schemas

users: {
    phone_number: string  # not present if it's a qr signup
    qr_code: strong  # not present if sms signup
    created: datetime
    last_checkin: datetime
}

smscodes: {
    code: string
    created: datetime
}

qrcodes: {
    code: string
    created: datetime
}

posts: {
    message: string
    poster_id: user_id
    submitted: datetime
    showtime: datetime  # not present if it hasn't been shown yet
    extender_ids: [user_ids]
}
"""


class InvalidCodeException(Exception):
    """When user uses code that doesn't exist"""


class NotCheckedInException(Exception):
    """When user tries to vote or post before checking in"""


class NoSuchUserException(Exception):
    """When user tries to vote or post before checking in"""


class ChillOut(Exception):
    """When users get too excited (try to re-up-vote or re-post too soon)."""


def create_sms_code():
    """Create a new code. More races woo!"""
    while True:
        code = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz1234567890')
                       for _ in range(6))
        existing_with_code = pymongo.db.smscodes.find_one({'code': code})
        if existing_with_code is None:
            break

    new_sms = {
        'code': code,
        'created': tznow()
    }
    pymongo.db.smscodes.insert(new_sms)
    socket_push(key='new_sms', val=new_sms['code'])
    return new_sms


def refresh_qr_code():
    """Create a new one"""
    while True:
        code = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz1234567890')
                       for _ in range(9))
        existing_with_code = pymongo.db.qrcodes.find_one({'code': code})
        if existing_with_code is None:
            break

    new_qr = {
        'code': code,
        'created': tznow()
    }
    pymongo.db.qrcodes.insert(new_qr)
    socket_push(key='new_qr', val=new_qr['code'])
    return new_qr


def get_qr_code():
    """Fetch the current qr code for display"""
    code = pymongo.db.qrcodes.find_one(sort=[('created', DESCENDING)])
    if code is None:
        code = refresh_qr_code()
    return code


def get_sms_code():
    """Fetches the most up-to-date SMS code for the billboard.

    This may trigger a new code if one is due.
    """
    codes = pymongo.db.smscodes.find(sort=[('created', DESCENDING)])
    try:
        current = next(codes)
    except StopIteration:  # empty database
        current = create_sms_code()
    if current['created'] + SMS_CODE_RESET < tznow():
        # yo, WARNING: off to the races!
        current = create_sms_code()
    return current


def get_queue():
    """Fetch all posts currently queued."""
    unshown = pymongo.db.posts.find({'showtime': {'$exists': False}})
    queue_in_order = unshown.sort('submitted')
    return queue_in_order


def get_user_from_phone(phone_number):
    """Get a user given their phone number, or None if they don't exist"""
    return pymongo.db.users.find_one({'phone_number': phone_number})

def get_user_from_user_id(userid):
    return  pymongo.db.users.find_one({'_id': userid})

def get_post_from_post_id(post_id):
    return  pymongo.db.posts.find_one({'_id': post_id})

def is_checked_in(user):
    """Test whether a user is checked in or not."""
    if user is None:
        return False
    a_ok = user['last_checkin'] + USER_CHECKIN_EXPIRE > tznow()
    return a_ok


def check_sms_code(test_code):
    """Checks whether the SMS code is currently valid."""
    codes = pymongo.db.smscodes.find(sort=[('created', DESCENDING)])
    current = next(codes)
    if test_code == current['code']:
        return True
    else:
        previous = next(codes)
        if (test_code == previous['code'] and
            tznow() - current['created'] < SMS_CODE_GRACE):
            return True
        else:
            return False


def check_in_with_sms_code(phone_number, code):
    """Check in (and possibly create) a user, verified by the active code.

    Returns the user's data, or raises InvalidCodeException if the code is wrong or expired.
    """
    if not check_sms_code(code):
        raise InvalidCodeException("You There has been an error")
    user = pymongo.db.users.find_one({'phone_number': phone_number})
    if user is None:  # so racey
        user = {
            'phone_number': phone_number,
            'created': tznow(),
        }
    user['last_checkin'] = tznow()
    pymongo.db.users.save(user)
    return user


def check_qr_code(test_code):
    """Validate a qr code for realz.

    QRs are immediately expired after one use.
    """
    current = pymongo.db.qrcodes.find_one(sort=[('created', DESCENDING)])
    if test_code == current['code']:
        refresh_qr_code()
        return True
    return False


def check_in_with_qr_code(user_id, code):
    """Check in an existing user with a QR code."""
    try:
        user_oid = ObjectId(user_id)
    except InvalidId:
        raise NoSuchUserException('nope nuttin')
    user = pymongo.db.users.find_one({'_id': ObjectId(user_id)})  ## ERRROROING
    if user is None:
        raise NoSuchUserException('no user exists with id {}'.format(user_id))
    if not check_qr_code(code):
        raise InvalidCodeException('You There has been an error -- wrong qr code yoyo')
    user['last_checkin'] = tznow()
    pymongo.db.users.save(user)


def create_account_with_qr_code(code):
    """Creates a new user with a QR code."""
    if not check_qr_code(code):
        raise InvalidCodeException('You There has been an error -- wrong qr code yooy')
    now = tznow()
    user = {
        'qr_code': code,
        'created': now,
        'last_checkin': now,
    }
    pymongo.db.users.insert(user)
    return user


def get_current_post():
    showing = pymongo.db.posts.find_one({'showtime': {'$exists': True}},
                                        sort=[('showtime', DESCENDING)])
    return showing


def update_showing():
    next_ = pymongo.db.posts.find_one({'showtime': {'$exists': False}},
                                      sort=[('submitted', ASCENDING)])
    if next_ is not None:
        print('changing...')
        pymongo.db.posts.update({'_id': next_['_id']},
                                {'$set': {'showtime': tznow()}})
    else:
        print('nothing in the queue')


    socket_push(key='new_message', val=get_current_post()['message'])


def post_message(user, message):
    """Try to queue a message for a user.

    Returns the message's position in the queue.

    Raises ChillOut if the user has posted too many messages recently.
    """
    user_id = user['_id']
    prev = pymongo.db.posts.find_one({'poster_id': user_id},
                                     sort=[('submitted', DESCENDING)])
    if (prev is not None and
        prev['submitted'] + USER_POST_THROTTLE > tznow()):
        raise ChillOut('Whoa. Chill out, hey. So many messages.')

    post = {
        'message': message,
        'poster_id': user_id,
        'submitted': tznow(),
        'extender_ids': [user_id],
    }
    pymongo.db.posts.insert(post)
    return get_queue().count()


def save_vote(user, post= None):
    """Register a vote for a user.

    Returns 1 if the vote was counted.

    Currently it is hard-coded to always succeed
    """

    if post is None:
        post = get_current_post();

    pymongo.db.posts.update({"_id": post["_id"]},
                                {"$addToSet":{"extender_ids":user["_id"]}});

    return 1




@app.route('/sms', methods=['GET','POST'])
def handle_sms():

    #Get number and response
    from_number = request.values['From']
    from_response = request.values['Body']
    first_word = from_response.lower().strip().split(' ', 1)[0];
    try:
        user_post = from_response.lower().strip().split(' ', 1)[1]
    except IndexError:
        user_post = None
    resp = twilio.twiml.Response()

    user = get_user_from_phone(from_number)

    #Checks if user already checked in

    if is_checked_in(user):
         #Check if user response is vote
        if "vote" in first_word:
            if save_vote(user):
                message="Vote successful"
            else:
                message="Vote unsuccessful"

        #Check if user response is a post
        elif "post" in first_word:
            if user_post is None:
                message = "whoops, no post provided, nothing to do."
                resp.message(message)
                return str(resp)
            try:
                queue_num = post_message(user, user_post)
            except ChillOut:
                message = "chill out dude. wait a bit, then post again."
                resp.message(message)
                return str(resp)
            message = "Your message is queued in position {}".format(queue_num)

        else:
            #check if code is correct
            try:
                check = check_in_with_sms_code(from_number, first_word);

            except InvalidCodeException:
                #error handling
                message="There has been an error, though you are already checked in..."
                resp.message(message)
                return str(resp)

            message = ("Thanks for checking in! To vote, Please text 'vote', "
                       "otherwise text 'post' and type in your message")

    #User hasn't checked in but is checking in now
    elif "post" not in first_word and "vote" not in first_word:
          #check if code is correct
            try:
                check = check_in_with_sms_code(from_number, first_word);
            except InvalidCodeException:
                #error handling
                message="There has been an error, and you are not even checked in :("
                resp.message(message)
                return str(resp)

            message = ("Thanks for checking in! To vote, Please text 'vote', "
                       "otherwise text 'post' and type in your message")
    else:
        #error handling
        message="Not checked in"
        resp.message(message)
        return str(resp)


    resp.message(message)

    return str(resp)


@app.route('/')
def home():
    resp = 'sms code: {}<br/>'.format(get_sms_code().get('code'))
    message = get_current_post()
    if message is not None:
        resp += '{} ({} votes)'.format(message['message'],
                                       len(message['extender_ids']))
    else:
        resp += 'No post yet :('
    return resp


@app.route('/webapp/get-id')
@crossdomain(origin='*')
def webapp_id():
    code = request.values.get('hash')
    if code is None:
        resp = jsonify(status='bad', message='missing code')
        resp.status_code = 400
        return resp
    try:
        user = create_account_with_qr_code(code)
    except InvalidCodeException:
        resp = jsonify(status='bad', message='invalid code')
        resp.status_code = 400
        return resp

    return jsonify(status='cool', userId=str(user['_id']))


@app.route('/webapp/check-in')
@crossdomain(origin='*')
def webapp_checkin():
    code = request.values.get('hash')
    if code is None:
        resp = jsonify(status='bad', message='missing code')
        resp.status_code = 400
        return resp
    user_id = request.values.get('userId')
    if user_id is None:
        resp = jsonify(status='bad', message='missing userId')
        resp.status_code = 400
        return resp
    try:
        check_in_with_qr_code(user_id, code)
    except NoSuchUserException:
        resp = jsonify(status='bad', message='no such user')
        resp.status_code = 400
        return resp
    except InvalidCodeException:
        resp = jsonify(status='bad', message='invalid code')
        resp.status_code = 400
        return resp
    return jsonify(status='cool')


#Endpoint to get all messages and ids from queue
@app.route('/webapp/cards')
@crossdomain(origin='*')
def webapp_cards():
    print('getting cards...')
    cards = list(get_queue())
    print('queued: {}', len(cards))
    card_messages=[]
    for card in cards:
        card_messages.append({
            "message": card['message'],
            "id": str(card['_id'])
        })
    return jsonify(status='cool', content=card_messages)


@app.route('/webapp/upvote', methods=['POST'])
@crossdomain(origin='*')
def webapp_vote():
    post_id = request.values['cardId'];

    user = get_user_from_user_id(ObjectId(request.values['userId']))
    if is_checked_in(user):
        post = get_post_from_post_id(post_id)
        save_vote(user,post)
        return jsonify(status='cool')
    else:
        resp = jsonify(response="error")
        resp.status_code = 400
        return resp

@app.route('/webapp/new-post', methods=['POST'])
@crossdomain(origin='*')
def webapp_post_message():
    """Try to queue a message for a user.

    Returns the message's position in the queue.

    Raises ChillOut if the user has posted too many messages recently.
    """
    user_id = request.values['userId']
    message = request.values['message']
    prev = pymongo.db.posts.find_one({'poster_id': user_id},
                                     sort=[('submitted', DESCENDING)])
    if (prev is not None and
        prev['submitted'] + USER_POST_THROTTLE > tznow()):
            resp = jsonify(response="error - too many attempts")
            resp.status_code = 400
            return resp

    post = {
        'message': message,
        'poster_id': user_id,
        'submitted': tznow(),
        'extender_ids': [user_id],
    }
    pymongo.db.posts.insert(post)
    return jsonify(status='cool')


@app.route('/display/')
def display_display_yo():
    return render_template('display.html')


@app.route('/display/pop')
def display_poppin():
    update_showing()
    return render_template('display.html')


@app.route('/display/info')
@crossdomain(origin='*')
def display_data():
    display_stuff = {
        'smsCode': (get_sms_code() or {}).get('code'),
        'qrCode': (get_qr_code() or {}).get('code'),
        'message': (get_current_post() or {}).get('message'),
    }
    return jsonify(status='cool', **display_stuff)


# dev stuff

@app.route('/preload')
def preload():
    """Pre-load some great posts"""
    messages = (
        "message one",
        "message_two",
        "message_three",
    )
    user = pymongo.db.users.find_one()  # doesn't matter who
    return str([post_message(user, m) for m in messages])


@app.route('/seed')
def seed():
    """seed some stuff"""
    # qr code
    # sms code
    # initial message
    app.pymongo.db.posts.insert({
        'message': 'The first post',
        'submitted': tznow() - timedelta(minutes=60),
        'showtime': tznow() - timedelta(minutes=50),
    })


def push():
    """Push a test request context"""
    ctx = app.test_request_context()
    ctx.push()
    return ctx


if __name__ == '__main__':
    app.run(debug=True)
