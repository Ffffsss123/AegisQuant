import unittest
from datetime import timedelta
from tests import test_options_observer as h
class OptionsToday(unittest.TestCase):
 setUp=h.Paper.setUp
 tearDown=h.Paper.tearDown
 def attempt(self,enabled=True,expired=False,paused=False):
  p=h.paper();day=h.NOW.date().isoformat();p['entry_after_session']=day
  p['temporary_entry_window']=dict(session=day,starts_at=(h.NOW-timedelta(minutes=1)).isoformat(),expires_at=(h.NOW+timedelta(minutes=-1 if expired else 10)).isoformat())
  if enabled:self.s['temporary_entry_session']=day
  self.e.enter(self.s,[h.candidate()],h.parent(),p,dict(paused=paused),h.NOW)
 def test_today_allowed_after_explicit_request(self):self.attempt();self.assertEqual(len(self.s['trades']),1)
 def test_without_request_original_date_guard_remains(self):self.attempt(enabled=False);self.assertFalse(self.s['trades'])
 def test_expiry_restores_original_guard(self):self.attempt(expired=True);self.assertFalse(self.s['trades'])
 def test_pause_not_cleared(self):self.attempt(paused=True);self.assertFalse(self.s['trades'])
 def test_duplicate_same_contract_not_submitted(self):self.attempt();self.attempt();self.assertEqual(len(self.s['trades']),1)
