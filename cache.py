import time


class CacheHelper:
    def __init__(self):
        self.cache = {}
        self.expiry_times = {}

    def set(self, key, value, expiry_in_seconds):
        self.cache[key] = value
        self.expiry_times[key] = time.time() + expiry_in_seconds

    def get(self, key):
        if self.check_expiry(key):
            return None
        else:
            return self.cache[key]

    def check_expiry(self, key):
        if key in self.expiry_times.keys():
            if time.time() > self.expiry_times[key]:
                del self.cache[key]
                del self.expiry_times[key]
                return True
            return False
        return True

    def delete(self, key):
        if key in self.cache:
            del self.cache[key]
        if key in self.expiry_times:
            del self.expiry_times[key]

    def pull(self, key):
        if (value := self.get(key)) is None:
            return None
        self.delete(key)
        return value
