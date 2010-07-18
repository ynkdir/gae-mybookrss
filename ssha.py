
import base64
import hashlib
import random


def ssha(password, salt=None):
    if salt is None:
        salt = generate_salt()
    salted_password = hashlib.sha1(password).digest() + salt
    return base64.b64encode(salted_password)


def equals(ssha_password, plain_password):
    salt = find_salt(ssha_password)
    return (ssha_password == ssha(plain_password, salt))


def find_salt(ssha_password):
    salted_password = base64.b64decode(ssha_password)
    digest_size = hashlib.sha1().digest_size
    return salted_password[digest_size:]


def generate_salt():
    chars = [chr(c) for c in range(256)]
    return "".join(random.choice(chars) for i in range(8))
