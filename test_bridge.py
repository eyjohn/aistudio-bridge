import asyncio
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure CREATE_NEW_PROCESS_GROUP is available on POSIX for testing Windows branches
if not hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
    subprocess.CREATE_NEW_PROCESS_GROUP = 512

# Provide mock dependencies for testing without external packages
if "websockets" not in sys.modules:
    sys.modules["websockets"] = MagicMock()

# Use normal package import layout
src_dir = str(Path(__file__).resolve().parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Import bridge module and ChromeBridge to support patch.object on bridge.os/bridge.signal
from aistudio_bridge import bridge
from aistudio_bridge.bridge import ChromeBridge


class TestChromeBridgeLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge = ChromeBridge(
            app_id="test_app",
            profile_dir="/tmp/test_profile",
            use_visuals=False,
            chrome_binary="google-chrome",
        )

    @unittest.skipIf(os.name == "nt", "POSIX-only test")
    @patch("aistudio_bridge.bridge.subprocess.Popen")
    def test_launch_browser_posix_mocked(self, mock_popen):
        with patch("aistudio_bridge.bridge.os.name", "posix"):
            with patch.object(
                bridge.os,
                "setsid",
                create=True,
                new=MagicMock(name="setsid")
            ):
                flags = ["--flag1", "--flag2"]
                self.bridge._launch_browser(flags)

                mock_popen.assert_called_once()

                self.assertIn("preexec_fn", mock_popen.call_args.kwargs)

    @unittest.skipIf(os.name == "nt", "POSIX-only test")
    @patch("aistudio_bridge.bridge.subprocess.Popen")
    def test_launch_browser_posix(self, mock_popen):
        with patch("aistudio_bridge.bridge.os.name", "posix"):
            flags = ["--flag1", "--flag2"]
            self.bridge._launch_browser(flags)

            mock_popen.assert_called_once_with(
                ["google-chrome", "--flag1", "--flag2"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )

    @patch("aistudio_bridge.bridge.os.killpg", create=True)
    def test_terminate_browser_windows(self, mock_killpg):
        with patch("aistudio_bridge.bridge.os.name", "nt"):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            self.bridge.chrome_proc = mock_proc

            self.bridge._terminate_browser()

            mock_proc.terminate.assert_called_once()
            mock_proc.wait.assert_called_once_with(timeout=5)
            mock_killpg.assert_not_called()
            self.assertIsNone(self.bridge.chrome_proc)

    @unittest.skipIf(os.name == "nt", "POSIX-only test")
    @patch("aistudio_bridge.bridge.os.killpg", create=True)
    @patch("aistudio_bridge.bridge.os.getpgid", create=True, return_value=12345)
    def test_terminate_browser_posix(self, mock_getpgid, mock_killpg):
        with patch("aistudio_bridge.bridge.os.name", "posix"):
            with patch.object(
                bridge.signal,
                "SIGKILL",
                9,
                create=True,
            ):
                mock_proc = MagicMock()
                mock_proc.pid = 999
                mock_proc.poll.return_value = None
                self.bridge.chrome_proc = mock_proc

                self.bridge._terminate_browser()

                mock_getpgid.assert_called_once_with(999)
                mock_killpg.assert_called_once_with(12345, 9)
                self.assertIsNone(self.bridge.chrome_proc)

    @patch("aistudio_bridge.bridge.subprocess.run")
    def test_cleanup_browser_windows(self, mock_run):
        with patch("aistudio_bridge.bridge.os.name", "nt"):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            self.bridge.chrome_proc = mock_proc

            self.bridge._cleanup_browser()

            mock_proc.terminate.assert_called_once()
            mock_proc.wait.assert_called_once_with(timeout=5)
            mock_run.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX-only test")
    @patch("aistudio_bridge.bridge.subprocess.run")
    def test_cleanup_browser_posix(self, mock_run):
        with patch("aistudio_bridge.bridge.os.name", "posix"):
            self.bridge._cleanup_browser()

            mock_run.assert_called_once_with(
                [
                    "pkill",
                    "-9",
                    "-f",
                    f"--user-data-dir={self.bridge.profile_dir}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    @unittest.skipIf(os.name == "nt", "POSIX-only test")
    @patch("aistudio_bridge.bridge.logger.warning")
    @patch("aistudio_bridge.bridge.subprocess.run", side_effect=FileNotFoundError)
    def test_cleanup_browser_posix_pkill_missing(self, mock_run, mock_warning):
        with patch("aistudio_bridge.bridge.os.name", "posix"):
            self.bridge._cleanup_browser()

            mock_run.assert_called_once()
            mock_warning.assert_called_once_with("pkill not available; skipping browser cleanup")

    @patch.object(ChromeBridge, "launch", new_callable=AsyncMock)
    @patch.object(ChromeBridge, "_terminate_browser")
    async def test_recover_delegates_to_terminate_and_relaunch(self, mock_terminate, mock_launch):
        mock_ws = AsyncMock()
        self.bridge.ws = mock_ws
        self.bridge.is_recovering = False

        await self.bridge.recover()

        mock_terminate.assert_called_once()
        mock_ws.close.assert_called_once()
        mock_launch.assert_called_once()
        self.assertFalse(self.bridge.is_recovering)

    @patch.object(ChromeBridge, "launch", new_callable=AsyncMock)
    @patch.object(ChromeBridge, "_terminate_browser")
    async def test_recover_skips_when_already_recovering(self, mock_terminate, mock_launch):
        self.bridge.is_recovering = True

        await self.bridge.recover()

        mock_terminate.assert_not_called()
        mock_launch.assert_not_called()
        self.assertTrue(self.bridge.is_recovering)


if __name__ == "__main__":
    unittest.main()
