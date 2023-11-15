import os
if os.environ.get("SERVER_SOFTWARE", "").startswith("Dev"):

    import pickle
    from google.appengine.ext.deferred import deferred

    def _deferred_serialize(obj, *args, **kwargs):
        curried = deferred._curry_callable(obj, *args, **kwargs)
        return pickle.dumps(curried, protocol=0)

    deferred.serialize = _deferred_serialize
