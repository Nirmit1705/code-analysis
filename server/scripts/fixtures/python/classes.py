class Greeter:
    """A greeter class."""

    def __init__(self, name):
        self.name = name

    def greet(self):
        """Says hi."""
        return f"Hi {self.name}"

    @staticmethod
    def create():
        return Greeter("x")

    @property
    def label(self):
        return self.name


class Empty:
    pass
