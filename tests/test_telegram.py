import datetime
import types

from jarvis import config
from jarvis.tools import telegram as tg

NS = types.SimpleNamespace


def test_nur_kanaele_keine_privaten_chats(monkeypatch):
    monkeypatch.setenv("JARVIS_TELEGRAM_API_ID", "1")
    monkeypatch.setenv("JARVIS_TELEGRAM_API_HASH", "x" * 32)
    (config.data_dir() / "telegram.session").touch()
    now = datetime.datetime.now(datetime.timezone.utc)
    h = datetime.timedelta(hours=1)
    chan = NS(broadcast=True, megagroup=False, username="ticker")
    group = NS(broadcast=False, megagroup=True, username="grp")
    user = NS(first_name="Privat")
    dialogs = [NS(entity=chan, name="Ticker", date=now - h), NS(entity=group, name="Gruppe", date=now - h),
               NS(entity=user, name="Privat", date=now)]
    msgs = {id(chan): [NS(id=5, date=now - h, message="DAX steigt")], id(group): [NS(id=9, date=now - h, message="DAX Gruppe")],
            id(user): [NS(id=1, date=now, message="DAX privat")]}

    class Client:
        def is_connected(self): return True
        async def connect(self): pass
        async def disconnect(self): pass
        async def is_user_authorized(self): return True

        async def iter_dialogs(self, limit=0):
            for d in dialogs:
                yield d

        async def iter_messages(self, entity, limit=0, search=None):
            for m in msgs[id(entity)][:limit]:
                yield m

    monkeypatch.setattr(tg, "_client", lambda: Client())
    monkeypatch.setitem(tg._shared, "client", None)
    out = tg.neuigkeiten()
    assert "DAX steigt" in out and "privat" not in out and "Gruppe" not in out
    assert "DAX Gruppe" in tg.neuigkeiten(mit_gruppen=True)
    assert [t.name for t in tg.tools()] == ["telegram_neuigkeiten", "telegram_suchen"]
