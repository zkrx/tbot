import abc
import typing
import shlex
import shutil
from tbot import log
from tbot import machine
from tbot.machine import channel
from .path import Path
from .special import Special
from . import shell as sh

Self = typing.TypeVar("Self", bound="LinuxMachine")


class LinuxMachine(machine.Machine, machine.InteractiveMachine):
    """Generic machine that is running Linux."""

    shell: typing.Type[sh.Shell] = sh.Bash

    @property
    @abc.abstractmethod
    def username(self) -> str:
        """Return the username for logging in on this machine."""
        pass

    @property
    @abc.abstractmethod
    def workdir(self: Self) -> Path[Self]:
        """Return a path where testcases can store data on this host."""
        pass

    @abc.abstractmethod
    def _obtain_channel(self) -> channel.Channel:
        pass

    def build_command(
        self,
        *args: typing.Union[str, Special, Path[Self]],
        stdout: typing.Optional[Path[Self]] = None,
    ) -> str:
        """
        Build the string representation of a command.

        :param args: Each arg is a token that will be sent to the shell. Can be
            either a str, a :class:`~linux.special.Special` or a Path that
            is associated with this host. Arguments will be escaped
            (a str like "a b" will not result in separate arguments to the
            command)
        :param Path stdout: File where stdout should be directed to
        :returns: A string that would be sent to the machine to execute the command
        :rtype: str
        """
        command = ""
        for arg in args:
            if isinstance(arg, Path):
                if arg.host is not self:
                    raise machine.WrongHostException(self, arg)

                arg = arg._local_str()

            if isinstance(arg, Special):
                command += arg.resolve_string() + " "
            else:
                command += f"{shlex.quote(arg)} "

        if isinstance(stdout, Path):
            if stdout.host is not self:
                raise Exception(
                    f"{self!r}: Provided {stdout!r} is not associated with this host"
                )
            stdout_file = stdout._local_str()
            command += f">{shlex.quote(stdout_file)} "

        return command[:-1]

    def exec(
        self: Self,
        *args: typing.Union[str, Special, Path[Self]],
        stdout: typing.Optional[Path[Self]] = None,
        timeout: typing.Optional[float] = None,
    ) -> typing.Tuple[int, str]:
        """
        Run a command on this machine.

        :param args: Each arg is a token that will be sent to the shell. Can be
            either a str, a :class:`~linux.special.Special` or a Path that
            is associated with this host. Arguments will be escaped
            (a str like "a b" will not result in separate arguments to the
            command)
        :param Path stdout: File where stdout should be directed to
        :returns: Tuple with the exit code and a string containing the combined
            stdout and stderr of the command (with a trailing newline).
        :rtype: (int, str)
        """
        channel = self._obtain_channel()

        command = self.build_command(*args, stdout=stdout)

        with log.command(self.name, command) as ev:
            ret, out = channel.raw_command_with_retval(
                command, stream=ev, timeout=timeout
            )

        return ret, out

    def exec0(
        self: Self,
        *args: typing.Union[str, Special, Path[Self]],
        stdout: typing.Optional[Path[Self]] = None,
        timeout: typing.Optional[float] = None,
    ) -> str:
        """
        Run a command on this machine and ensure it succeeds.

        :param args: Each arg is a token that will be sent to the shell. Can be
            either a str, a :class:`~linux.special.Special` or a Path that
            is associated with this host. Arguments will be escaped
            (a str like "a b" will not result in separate arguments to the
            command)
        :param Path stdout: File where stdout should be directed to
        :returns: A string containing the combined stdout and stderr of the
            command (with a trailing newline).
        :rtype: str
        """
        ret, out = self.exec(*args, stdout=stdout, timeout=timeout)

        if ret != 0:
            raise machine.CommandFailedException(self, self.build_command(*args), out)

        return out

    def interactive(self) -> None:
        """Drop into an interactive session on this machine."""
        channel = self._obtain_channel()

        # Generate the endstring instead of having it as a constant
        # so opening this files won't trigger an exit
        endstr = "INTERACTIVE-END-" + hex(165380656580165943945649390069628824191)[2:]

        size = shutil.get_terminal_size()
        cmd = self.shell.enable_editing()
        if cmd is not None:
            channel.raw_command(cmd)

        channel.raw_command(f"stty cols {size.columns}; stty rows {size.lines}")
        channel.send(f"{self.shell.name}\n")
        cmd = self.shell.disable_history()
        if cmd is not None:
            channel.send(f"{cmd}\n")

        channel.send(self.shell.set_prompt(endstr) + "\n")
        channel.read_until_prompt(endstr)

        channel.send(f"{self.shell.name}\n")
        cmd = self.shell.set_prompt(
            f"\\[\\033[36m\\]{self.name}: \\[\\033[32m\\]\\w\\[\\033[0m\\]> "
        )
        channel.send(f"{cmd}\n")
        channel.read_until_prompt("> (\x1B\\[.*)?", regex=True)
        channel.send("\n")
        log.message("Entering interactive shell ...")

        channel.attach_interactive(end_magic=endstr)

        log.message("Exiting interactive shell ...")

        try:
            channel.raw_command("exit\n", timeout=0.5)
        except TimeoutError:
            raise RuntimeError("Failed to reacquire shell after interactive session!")

        cmd = self.shell.disable_editing()
        if cmd is not None:
            channel.raw_command(cmd)