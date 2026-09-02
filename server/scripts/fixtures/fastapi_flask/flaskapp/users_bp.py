from flask import Blueprint

bp = Blueprint("users", __name__, url_prefix="/users")


@bp.route("/", methods=["GET"])
def list_users():
    pass


@bp.get("/<int:user_id>")
def get_user(user_id):
    pass


@bp.route("/", methods=["POST"])
def create_user():
    pass
