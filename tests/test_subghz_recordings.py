"""Offline tests; never open or transmit through real RF hardware."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import subghz
import wifi


class Recordings(unittest.TestCase):
    def setUp(self):
        self.data = dict(format='zerogotchi-ook-v1', frequency=430.5,
                         pulses=[500, -500, 1000, -1000])

    def test_private_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(wifi, 'DATA', Path(folder)):
            first = subghz.save_record(self.data)
            second = subghz.save_record(self.data)
            self.assertNotEqual(first, second)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            self.assertEqual(subghz.load_record(first), self.data)
            self.assertEqual(len(subghz.recordings()), 2)

    def test_invalid_recordings(self):
        for pulses in ([], [0, 100], [True, -1], [15_000_000, -1], ['1', -1]):
            with self.subTest(pulses=pulses), self.assertRaises(wifi.WifiError):
                subghz.validate_record({**self.data, 'pulses': pulses})

    def test_encoding_preserves_cumulative_duration_and_levels(self):
        payload = subghz.encode_record(self.data)
        bits = ''.join(f'{value:08b}' for value in payload)
        self.assertEqual(bits[:10], '1' * 10)
        self.assertEqual(bits[10:20], '0' * 10)
        self.assertEqual(bits[20:40], '1' * 20)
        self.assertEqual(bits[40:60], '0' * 20)
        self.assertLessEqual(abs((len(payload)-2)*8/subghz.REPLAY_RATE - .003), 8/subghz.REPLAY_RATE)

    def test_capture_timestamps_and_rejects_missing_edges(self):
        for missing in (False, True):
            cancel = threading.Event()
            events = [SimpleNamespace(line_seqno=n, timestamp_ns=t*1000, event_type=level)
                      for n,t,level in [(1,1000,1),(3 if missing else 2,1350,0),(4 if missing else 3,2400,1)]]
            class Lines:
                def wait_edge_events(self, timeout): return timeout != 0
                def read_edge_events(self, max_events):
                    cancel.set()
                    return events
            radio = SimpleNamespace(lines=Lines(), recoveries=0, write=lambda *args: None,
                                    tune=lambda mhz: None, sample=lambda: -40)
            @contextmanager
            def receiver(profile, **kwargs): yield radio
            gpio = SimpleNamespace(EdgeEvent=SimpleNamespace(Type=SimpleNamespace(RISING_EDGE=1)))
            with patch.object(subghz, 'receiver', receiver), patch.dict('sys.modules', gpiod=gpio):
                if missing:
                    with self.assertRaisesRegex(wifi.WifiError, 'lost GPIO edges'):
                        subghz.record('rf2-v1',315,-70,cancel,lambda _: None)
                else:
                    data = subghz.record('rf2-v1',315,-70,cancel,lambda _: None)
                    self.assertEqual(data['pulses'], [350,-1050])

    def test_repeat_stops_during_gap_and_propagates_radio_errors(self):
        cancel = threading.Event()
        messages = []
        with patch.object(subghz, 'replay', return_value='complete') as send, patch.object(cancel, 'wait', side_effect=lambda seconds: cancel.set() or True):
            result = subghz.replay_until_cancel('rf2-v1',self.data,cancel,messages.append)
            send.assert_called_once()
            self.assertIn('1 completed',result)
        cancel.clear()
        def fail(*args): raise wifi.WifiError('radio failed')
        with patch.object(subghz,'replay',side_effect=fail) as send:
            with self.assertRaisesRegex(wifi.WifiError,'radio failed'):
                subghz.replay_until_cancel('rf2-v1',self.data,cancel,messages.append)
            send.assert_called_once()
        cancel.set()
        with patch.object(subghz,'replay') as send:
            subghz.replay_until_cancel('rf2-v1',self.data,cancel,messages.append)
            send.assert_not_called()

    def test_replay_menu_defaults(self):
        import runpy
        app_type = runpy.run_path('app/zerogotchi')['App']
        app = app_type.__new__(app_type)
        app.subghz_record_result(self.data,Path('test.sub'),lambda:None)
        self.assertEqual(app.index,0)
        self.assertEqual([name for name,_ in app.items],['Replay','Erase'])
        app.items[0][1]()
        self.assertEqual(app.index,0)
        self.assertEqual([name for name,_ in app.items],['Replay Once','Replay Until Cancel','Cancel'])
        with patch.object(app,'work') as work,patch.object(subghz,'hat_key',return_value='rf2-v1'),patch.object(subghz,'replay_until_cancel') as repeat:
            app.cancel=threading.Event();app.report_capture=lambda _:None
            app.items[1][1]()
            operation=work.call_args.args[2]
            operation()
            repeat.assert_called_once()
            self.assertTrue(work.call_args.kwargs['cancellable'])

    def test_replay_drains_fifo_and_stops_on_cancel_or_underrun(self):
        class Radio:
            def __init__(self, counts):
                self.counts = iter(counts)
                self.commands = []
            def tune(self, mhz): pass
            def write(self, *args): pass
            def transfer(self, data): self.commands.append(tuple(data))
            def strobe(self, command): self.commands.append(command)
            def read(self, register): return next(self.counts)
        for cancel, pulses, counts, failed in (
            (False, self.data['pulses'], [0x80, 0x80], False),
            (True, self.data['pulses'], [], False),
            (False, [100_000, -100_000], [0x80, 0x80], True),
        ):
            radio = Radio(counts)
            @contextmanager
            def receiver(profile): yield radio
            event = threading.Event()
            if cancel: event.set()
            with patch.object(subghz, 'receiver', receiver):
                if failed:
                    with self.assertRaises(wifi.WifiError):
                        subghz.replay('rf2-v1', {**self.data, 'pulses': pulses}, event, lambda _: None)
                else:
                    subghz.replay('rf2-v1', {**self.data, 'pulses': pulses}, event, lambda _: None)
            self.assertEqual(radio.commands[-2:], [0x36, 0x3b])
            self.assertFalse(subghz.TX_ACTIVE.is_set())


if __name__ == '__main__':
    unittest.main()
