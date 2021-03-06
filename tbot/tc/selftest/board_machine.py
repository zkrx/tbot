# tbot, Embedded Automation Tool
# Copyright (C) 2019  Harald Seiler
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import contextlib
import typing

import tbot
from tbot.machine import channel, linux, board, connector
from tbot.tc import selftest
from . import machine as mach


class DummyConnector(connector.Connector):
    def __init__(self, mach: linux.LinuxShell, autoboot: bool = True) -> None:
        self.mach = mach
        self.autoboot = autoboot

    @contextlib.contextmanager
    def _connect(self) -> typing.Iterator[channel.Channel]:
        with self.mach.clone() as cloned:
            ch = cloned.ch.take()
            tbot.log_event.command(self.mach.name, "dummy-connect")
            if self.autoboot:
                ch.sendline(
                    """\
bash --norc --noprofile --noediting; exit
unset HISTFILE
PS1='Test-U-Boot> '
alias version="uname -a"
function printenv() {
    if [ $# = 0 ]; then
        set | grep -E '^U'
    else
        set | grep "$1" | sed "s/'//g"
    fi
}
function setenv() { local var="$1"; shift; eval "$var=\\"$*\\""
}
bash --norc --noprofile --noediting""",
                    read_back=True,
                )
                ch.sendline(
                    """\
unset HISTFILE
set +o emacs
set +o vi
read -p 'Autoboot: '; exit""",
                    read_back=True,
                )
            else:
                ch.sendline(
                    """\
bash --norc --noprofile --noediting; exit
unset HISTFILE
alias version="uname -a"
function printenv() {
    if [ $# = 0 ]; then
        set | grep -E '^U'
    else
        set | grep "$1" | sed "s/'//g"
    fi
}
function setenv() { local var="$1"; shift; eval "$var=\\"$*\\""
}
PS1=Test-U-Boot'> '""",
                    read_back=True,
                )
                ch.read_until_prompt("Test-U-Boot> ")

            yield ch

    def clone(self) -> typing.NoReturn:
        raise NotImplementedError("can't clone a serial connection")


class TestBoard(DummyConnector, board.Board):
    """Dummy Board."""

    name = "test"


class TestBoardUBoot(board.Connector, board.UBootAutobootIntercept, board.UBootShell):
    """Dummy Board UBoot."""

    name = "test-ub"

    autoboot_prompt = tbot.Re("Autoboot: ")
    prompt = "Test-U-Boot> "


@tbot.testcase
def selftest_board_uboot(lab: typing.Optional[tbot.selectable.LabHost] = None) -> None:
    """Test if tbot intercepts U-Boot correctly."""

    with contextlib.ExitStack() as cx:
        lh = cx.enter_context(lab or tbot.acquire_lab())
        try:
            b: board.Board = cx.enter_context(tbot.acquire_board(lh))
            ub: board.UBootShell = cx.enter_context(
                tbot.acquire_uboot(b)  # type: ignore
            )
        except NotImplementedError:
            b = cx.enter_context(TestBoard(lh))
            ub = cx.enter_context(TestBoardUBoot(b))

        ub.exec0("version")
        env = ub.exec0("printenv").strip().split("\n")

        for line in env[:-1]:
            if line != "" and line[0].isalnum():
                assert "=" in line, repr(line)

        out = ub.exec0("echo", hex(0x1234)).strip()
        assert out == "0x1234", repr(out)

        mach.selftest_machine_shell(ub)


@tbot.testcase
def selftest_board_uboot_noab(
    lab: typing.Optional[tbot.selectable.LabHost] = None,
) -> None:
    """Test if tbot intercepts U-Boot correctly without autoboot."""

    class TestUBootNoAB(DummyConnector, board.UBootShell):
        """Dummy Board UBoot."""

        name = "test-ub-noab"

        prompt = "Test-U-Boot> "

    with contextlib.ExitStack() as cx:
        lh = cx.enter_context(lab or tbot.acquire_lab())
        ub = cx.enter_context(TestUBootNoAB(lh, autoboot=False))

        ub.exec0("version")
        env = ub.exec0("printenv").strip().split("\n")

        for line in env[:-1]:
            if line != "" and line[0].isalnum():
                assert "=" in line, repr(line)

        out = ub.exec0("echo", hex(0x1234)).strip()
        assert out == "0x1234", repr(out)

        mach.selftest_machine_shell(ub)


@tbot.testcase
def selftest_board_linux(lab: typing.Optional[tbot.selectable.LabHost] = None) -> None:
    """Test board's linux."""

    with contextlib.ExitStack() as cx:
        lh = cx.enter_context(lab or tbot.acquire_lab())

        try:
            b = cx.enter_context(tbot.acquire_board(lh))
        except NotImplementedError:
            tbot.skip("No board available")

        lnx = cx.enter_context(tbot.acquire_linux(b))

        mach.selftest_machine_shell(lnx)


@tbot.testcase
def selftest_board_power(lab: typing.Optional[tbot.selectable.LabHost] = None) -> None:
    """Test if the board is powered on and off correctly."""

    class TestPowerUBoot(
        DummyConnector,
        board.PowerControl,
        board.UBootAutobootIntercept,
        board.UBootShell,
    ):
        """Dummy Board UBoot."""

        name = "test-ub-power"

        autoboot_prompt = tbot.Re("Autoboot: ")
        prompt = "Test-U-Boot> "

        def poweron(self) -> None:
            self.mach.exec0("touch", self.mach.workdir / "selftest_power")

        def poweroff(self) -> None:
            self.mach.exec0("rm", self.mach.workdir / "selftest_power")

    with lab or selftest.SelftestHost() as lh:
        power_path = lh.workdir / "selftest_power"
        if power_path.exists():
            lh.exec0("rm", power_path)

        tbot.log.message("Emulating a normal run ...")
        assert not power_path.exists()
        with TestPowerUBoot(lh):
            assert power_path.exists()

        assert not power_path.exists()

        class TestException(Exception):
            pass

        tbot.log.message("Emulating a failing run ...")
        try:
            with TestPowerUBoot(lh):
                assert power_path.exists()
                tbot.log.message("raise TestException()")
                raise TestException()
        except TestException:
            pass

        assert not power_path.exists()


class TestBoardLinuxUB(board.LinuxUbootConnector, board.LinuxBootLogin, linux.Bash):
    """Dummy board linux uboot."""

    uboot = TestBoardUBoot

    def do_boot(self, ub: board.UBootShell) -> channel.Channel:
        ub.exec0("echo", "Booting linux ...")
        ub.exec0("echo", "[  0.000]", "boot: message")
        ub.exec0("echo", "[  0.013]", "boot: info")
        ub.exec0("echo", "[  0.157]", "boot: message")
        return ub.boot(
            board.Raw(
                "printf 'tb-login: '; read username; printf 'Password: '; read password; [[ $username = 'root' && $password = 'rootpw' ]] || exit 1"
            )
        )

    username = "root"
    password = "rootpw"
    login_prompt = "tb-login: "

    @property
    def workdir(self) -> "linux.Path[TestBoardLinuxUB]":
        """Return workdir."""
        return linux.Workdir.static(self, "/tmp/tbot-wd")


@tbot.testcase
def selftest_board_linux_uboot(
    lab: typing.Optional[tbot.selectable.LabHost] = None,
) -> None:
    """Test linux booting from U-Boot."""

    with lab or selftest.SelftestHost() as lh:
        tbot.log.message("Testing without UB ...")
        with TestBoard(lh) as b:
            with TestBoardLinuxUB(b) as lnx:
                lnx.exec0("uname", "-a")

        tbot.log.message("Testing with UB ...")
        with TestBoard(lh) as b:
            with TestBoardUBoot(b) as ub:
                with TestBoardLinuxUB(ub) as lnx:
                    lnx.exec0("uname", "-a")
                    lnx.exec0("ls", lnx.workdir)


@tbot.testcase
def selftest_board_linux_nopw(
    lab: typing.Optional[tbot.selectable.LabHost] = None,
) -> None:
    """Test linux without a password."""

    class TestBoardLinuxUB_NOPW(
        board.LinuxUbootConnector, board.LinuxBootLogin, linux.Bash
    ):
        uboot = TestBoardUBoot

        def do_boot(self, ub: board.UBootShell) -> channel.Channel:
            ub.exec0("echo", "Booting linux ...")
            ub.exec0("echo", "[  0.000]", "boot: message")
            ub.exec0("echo", "[  0.013]", "boot: info")
            ub.exec0("echo", "[  0.157]", "boot: message")
            ub.exec0("export", "HOME=/tmp")
            return ub.boot(
                board.Raw(
                    "printf 'tb-login: '; read username; [[ $username = 'root' ]] || exit 1"
                )
            )

        username = "root"
        password = None
        login_prompt = "tb-login: "

        @property
        def workdir(self) -> "linux.Path[TestBoardLinuxUB_NOPW]":
            """Return workdir."""
            return linux.Workdir.athome(self, "tbot-wd")

    with lab or selftest.SelftestHost() as lh:
        tbot.log.message("Testing without UB ...")
        with TestBoard(lh) as b:
            with TestBoardLinuxUB_NOPW(b) as lnx:
                lnx.exec0("uname", "-a")

        tbot.log.message("Testing with UB ...")
        with TestBoard(lh) as b:
            with TestBoardUBoot(b) as ub:
                with TestBoardLinuxUB_NOPW(ub) as lnx:
                    lnx.exec0("uname", "-a")
                    lnx.exec0("ls", lnx.workdir)


@tbot.testcase
def selftest_board_linux_standalone(
    lab: typing.Optional[tbot.selectable.LabHost] = None,
) -> None:
    """Test linux booting standalone."""

    class TestBoardLinuxStandalone(board.Connector, board.LinuxBootLogin, linux.Bash):
        username = "root"
        password = None
        login_prompt = "Autoboot: "

    with lab or selftest.SelftestHost() as lh:
        tbot.log.message("Testing without UB ...")
        with TestBoard(lh) as b:
            with TestBoardLinuxStandalone(b) as lnx:
                lnx.exec0("uname", "-a")

        tbot.log.message("Testing with UB ...")
        with TestBoard(lh) as b:
            with TestBoardUBoot(b) as ub:
                raised = False
                try:
                    with TestBoardLinuxStandalone(ub) as lnx:  # type: ignore
                        lnx.exec0("uname", "-a")
                except Exception:
                    raised = True
                assert raised


@tbot.testcase
def selftest_board_linux_bad_console(
    lab: typing.Optional[tbot.selectable.LabHost] = None,
) -> None:
    """Test linux booting standalone."""

    tbot.skip("board-linux bad console test is not implemented")

    class BadBoard(connector.ConsoleConnector, board.Board):
        def connect(self, mach: linux.LinuxShell) -> channel.Channel:  # noqa: D102
            return mach.open_channel(
                linux.Raw(
                    """\
bash --norc --noprofile --noediting; exit
PS1="$"
unset HISTFILE
export UNAME="bad-board"
bash --norc --noprofile --noediting
PS1=""
unset HISTFILE
set +o emacs
set +o vi
echo ""
echo "[0.127] We will go into test mode now ..."
echo "[0.128] Let's see if I can behave bad enough to break you"
read -p 'bad-board login: [0.129] No clean login prompt for you';\
sleep 0.02;\
echo "[0.1337] Oh you though it was this easy?";\
read -p "Password: [0.ORLY?] Password ain't clean either you fool
It's even worse tbh";\
sleep 0.02;\
echo "[0.512] I have one last trick >:|";\
sleep 0.2;\
read -p ""\
"""
                )
            )

    class BadBoardLinux(board.Connector, board.LinuxBootLogin, linux.Bash):
        username = "root"
        password = "toor"
        login_delay = 1

    with lab or selftest.SelftestHost() as lh:
        with BadBoard(lh) as b:
            with BadBoardLinux(b) as lnx:
                name = lnx.env("UNAME")
                assert name == "bad-board", repr(name)
