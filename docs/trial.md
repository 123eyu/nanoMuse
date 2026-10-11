# One account, all your devices

Sign in with the same account on your phone, your computers and the web console, and
they work as one: say it on one device and it gets done on another, approvals come back to
the device where you typed, and the main conversation follows you. This page walks
through it on the public relay, `cloud.nanomuse.cn`, from a fresh phone to "the phone tells
the Mac to build the project". The protocol is [hub.md](hub.md); how the account and the
free pool work is [cloud.md](cloud.md); a relay of your own is [self-hosting.md](self-hosting.md).

## 1. The phone

Install the app ([android.md](android.md), [ios.md](ios.md)). The first screen is the
account: a phone number or an e-mail address and a code, or a password once you have set
one. That single sign-in sets up the nanoMuse Cloud provider, the four rows of *Settings →
Models* and the connection to the other devices.

*Settings → nanoMuse Cloud → Devices* is where the phone's side lives: *Reachable from your
devices* keeps the phone connected so the others can ask it for things (a quiet
notification shows while it is on); *Let other devices operate this phone* decides whether
they may run a shell command, read files, take a screenshot or hand over a task here, and
off, the phone still sees and drives the others; *This phone's name* is what you will say
out loud, so make it short: "pixel", "小米". The list under it is every device of the
account, online or when it was last seen.

The iPhone takes part with what iOS allows: it answers `info`, `open`, `notify` and `task`;
shell, files and the screen are not available on iOS.

## 2. The computer

Install nanoMuse Desktop from the [latest release](https://github.com/nano-muse/nanoMuse/releases/latest)
([desktop.md](desktop.md): one installer per platform, the runtime for the hands inside)
and sign in with the same account. Within seconds the computer is in the phone's Devices
list and the phone in the desktop's *Devices* page in the rail. Name the computer in
*Settings → Devices* ("mac", "desk").

The computer asks before another device operates it: a card on its screen, *once* or
*always for that device*. *Remote control without asking* in *Settings → Devices* turns the
card off for every device of the account.

A computer that runs only the Python runtime (`nanomuse serve`, with its web app signed in
to the same account) joins the same way ([app.md](app.md), [cli.md](cli.md)).

## 3. The web console

`https://cloud.nanomuse.cn/app/`, the same sign-in. It shows the account's devices and
conversations; pick a device, type, and approvals show as cards. A relay of your own serves
the same console at its `/app/`.

## 4. Try it

On the phone: 「在 mac 上列一下下载文件夹」 「让 desk 把 ~/proj 编译一遍，把最后二十行日志发我」
「电脑截个图给我看」 「给电脑发个通知：该睡了」

On the computer: "on the phone, take a screenshot", "tell pixel's Muse to read me the last
notification", "send pixel a notification: build finished".

In the console: pick a device, type.

A whole job in words ("compile the project and send me the log") runs as a task in the
other device's own Muse, in a conversation of its own, and may take minutes; the steps
show in your chat as they happen.

## 5. Approvals

Anything that deletes, sends, pays or touches the system asks first, on the device where
you typed it, and a remote Muse's approval question travels back to you the same way.
Operating a device from another one is a second question, asked of the person holding that
device (section 1 and 2 above). Nobody there, and the caller hears that it was not allowed.

## 6. What follows you

Signed in, the main conversation follows you to your other devices, and the side chats too
once *Also sync side chats* is on; *Data controls* on each device holds both switches
([sync.md](sync.md)). The agent's name and face
are the account's. A connector signed in on one device shows on the others as *Connected
on 〈device〉*, one tap to sign in there too ([hub.md](hub.md)). Models are chosen per device:
each one's *Settings → Models* is its own.

## 7. Your own relay

[self-hosting.md](self-hosting.md) runs the same relay on a small VPS. The phones take its
address under *Use a different server* on the sign-in screen; nanoMuse Desktop takes it in
the desktop profile's `cordis.patch.yml`; the console is whatever relay serves it at `/app/`.
Every device of one account must point at the same relay, since the devices and the
conversations live there.
