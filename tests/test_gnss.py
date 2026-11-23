"""Exercise GPS through a real PTY; no radio or GPS hardware is required."""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import pty
import runpy
import select
import tempfile
import termios
import unittest
import threading
from unittest.mock import patch

import gnss
import wifi


def nmea(body):
    data=body.encode('ascii');checksum=0
    for byte in data: checksum ^= byte
    return b'$'+data+b'*'+f'{checksum:02X}'.encode()+b'\r\n'


@contextmanager
def receiver_fixture(route='1', power='0'):
    with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
        base=Path(temporary);master,slave=pty.openpty()
        try:
            for key,value in [('CMDLINE','console=tty1'),('GROVE_ROUTE',route),('GROVE_POWER',power),('RAIL','1')]:
                path=base/key;path.write_text(value);stack.enter_context(patch.object(gnss,key,path))
            stack.enter_context(patch.object(gnss,'PORT',Path(os.ttyname(slave))))
            yield master,slave
        finally:
            os.close(master);os.close(slave)


class Gnss(unittest.TestCase):
    def test_grove_receives_fragmented_nmea_and_restores_port(self):
        with receiver_fixture() as (master,slave):
            initial=termios.tcgetattr(slave);initial[2]|=termios.CSTOPB;initial[0]|=termios.IXOFF
            termios.tcsetattr(slave,termios.TCSANOW,initial)
            initial=termios.tcgetattr(slave)
            monitor=gnss.GnssMonitor('/unused','',source='grove')
            try:
                self.assertIsNotNone(monitor.fd,monitor.error)
                self.assertEqual(gnss.GROVE_ROUTE.read_text(),'0');self.assertEqual(gnss.GROVE_POWER.read_text(),'1')
                mode=termios.tcgetattr(monitor.fd)
                self.assertEqual(mode[4:6],[termios.B115200]*2)
                self.assertEqual(mode[2]&termios.CSIZE,termios.CS8)
                self.assertFalse(mode[2]&(termios.PARENB|termios.CSTOPB))
                self.assertFalse(mode[0]&(termios.IXON|termios.IXOFF))
                sentence=nmea('GNGGA,123519,4807.038,N,01131.000,E,1,12,0.9,545.4,M,46.9,M,,')
                os.write(master,sentence[:22]);select.select([monitor.fd],[],[],1)
                self.assertFalse(monitor.poll(10))
                os.write(master,sentence[22:]+nmea('BDGSV,1,1,07')+nmea('GPGSV,1,1,08'))
                select.select([monitor.fd],[],[],1);self.assertTrue(monitor.poll(11))
                fix=monitor.position(11);self.assertAlmostEqual(fix.latitude,48.1173)
                self.assertAlmostEqual(fix.longitude,11.5166667)
                self.assertEqual(monitor.satellites(11),15)
                self.assertIsNone(monitor.position(17))
                self.assertIsNone(monitor.satellites(22))
            finally:monitor.close()
            self.assertEqual(gnss.GROVE_ROUTE.read_text(),'1');self.assertEqual(gnss.GROVE_POWER.read_text(),'0')
            restored=termios.tcgetattr(slave)
            # BSD PTYs can set the kernel PENDIN status bit on raw->canonical transition.
            restored[3] &= ~getattr(termios,'PENDIN',0)
            initial[3] &= ~getattr(termios,'PENDIN',0)
            self.assertEqual(restored,initial)

    def test_diagnostic_does_not_power_or_switch_grove(self):
        with receiver_fixture():
            monitor=gnss.GnssMonitor('/unused','',source='grove',enable_power=False)
            self.assertIsNone(monitor.fd);self.assertIn('inactive',monitor.error)
            self.assertEqual(gnss.GROVE_ROUTE.read_text(),'1');self.assertEqual(gnss.GROVE_POWER.read_text(),'0')

    def test_already_active_grove_is_not_powered_off_on_close(self):
        with receiver_fixture('0','1'):
            monitor=gnss.GnssMonitor('/unused','',source='grove');self.assertIsNotNone(monitor.fd,monitor.error)
            monitor.close();monitor.close()
            self.assertEqual(gnss.GROVE_ROUTE.read_text(),'0');self.assertEqual(gnss.GROVE_POWER.read_text(),'1')

    def test_gpio_led_full_brightness_is_treated_as_on(self):
        with receiver_fixture('0','255'):
            monitor=gnss.GnssMonitor('/unused','',source='grove',enable_power=False)
            self.assertIsNotNone(monitor.fd,monitor.error);monitor.close()
            self.assertEqual(gnss.GROVE_POWER.read_text(),'255')
        with receiver_fixture('255','0'):
            monitor=gnss.GnssMonitor('/unused','',source='grove')
            self.assertIsNotNone(monitor.fd,monitor.error);monitor.close()
            self.assertEqual(gnss.GROVE_ROUTE.read_text(),'255')

    def test_setup_failure_rolls_back_mux(self):
        with receiver_fixture():
            original=Path.write_text
            def fail_power(path,text,*args,**kwargs):
                if path==gnss.GROVE_POWER and text=='1':raise PermissionError('fixture')
                return original(path,text,*args,**kwargs)
            with patch.object(Path,'write_text',fail_power):
                monitor=gnss.GnssMonitor('/unused','',source='grove')
            self.assertIsNone(monitor.fd);self.assertIn('GPS access failed',monitor.error)
            self.assertEqual(gnss.GROVE_ROUTE.read_text(),'1');self.assertEqual(gnss.GROVE_POWER.read_text(),'0')

    def test_console_is_never_reconfigured(self):
        with receiver_fixture():
            gnss.CMDLINE.write_text('console=serial0,115200')
            monitor=gnss.GnssMonitor('/unused','',source='grove')
            self.assertIsNone(monitor.fd);self.assertIn('console',monitor.error)
            self.assertEqual(gnss.GROVE_ROUTE.read_text(),'1');self.assertEqual(gnss.GROVE_POWER.read_text(),'0')

    def test_second_reader_cannot_change_an_active_grove_session(self):
        with receiver_fixture():
            first=gnss.GnssMonitor('/unused','',source='grove')
            try:
                second=gnss.GnssMonitor('/unused','',source='grove')
                self.assertIsNone(second.fd)
                self.assertIn('already in use',second.error)
                self.assertEqual(gnss.GROVE_ROUTE.read_text(),'0');self.assertEqual(gnss.GROVE_POWER.read_text(),'1')
            finally:first.close()

    def test_bad_checksum_and_no_fix_are_not_logged(self):
        self.assertFalse(gnss.valid_sentence(b'$GNGGA,invalid*00'))
        line=nmea('GNGGA,123519,4807.038,N,01131.000,E,0,00,9.9,0,M,0,M,,').strip()
        self.assertTrue(gnss.valid_sentence(line));self.assertIsNone(gnss.parse_fix(line,1))

    def test_existing_cap_does_not_touch_grove(self):
        with receiver_fixture(),patch.object(gnss.subprocess,'Popen') as helper:
            monitor=gnss.GnssMonitor('/unused','',source='cap',enable_power=False)
            self.assertIsNotNone(monitor.fd,monitor.error);monitor.close()
            helper.assert_not_called()
            self.assertEqual(gnss.GROVE_ROUTE.read_text(),'1');self.assertEqual(gnss.GROVE_POWER.read_text(),'0')

    def test_cancelled_check_releases_grove_controls(self):
        with receiver_fixture(),patch.object(gnss,'source_key',return_value='grove'):
            cancel=threading.Event();cancel.set()
            result=gnss.check('/unused','',cancel,lambda _:None)
            self.assertIn('Grove GPS v1.1',result)
            self.assertEqual(gnss.GROVE_ROUTE.read_text(),'1');self.assertEqual(gnss.GROVE_POWER.read_text(),'0')

    def test_profile_setting_round_trip(self):
        ui=runpy.run_path('app/zerogotchi');app=ui['App'].__new__(ui['App']);app.settings=lambda:None
        with tempfile.TemporaryDirectory() as temporary,patch.object(wifi,'CONFIG',Path(temporary)):
            self.assertEqual(gnss.source_key(),'cap')
            app.gps_source();self.assertEqual(app.index,0)
            app.items[1][1]();self.assertEqual(gnss.source_key(),'grove')
            app.gps_source();self.assertEqual(app.index,1)


if __name__=='__main__':unittest.main()
