from . import items
from ..models import User
import os
import requests


def get_user():
    return User()


def list_all():
    return items.list_items()
