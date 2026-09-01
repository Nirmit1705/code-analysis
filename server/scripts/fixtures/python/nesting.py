def outer():
    def inner():
        return 1

    return inner


class Outer:
    class Inner:
        def method(self):
            return 1
