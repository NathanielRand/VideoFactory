// Text fields that stop taking typing after a message box.
//
// Electron on Windows: when a page calls alert(), confirm() or prompt(), the
// native box takes keyboard focus, and when it closes the window does not get
// it back. The page looks normal, a field even shows its caret, but keystrokes
// go nowhere until the window is clicked away from and back to. Every
// "Could not delete", "Delete this clip?" and "Replace the thumbnail?" in the
// app is one of these, and after any of them, typing dies.
//
// The fix is to give the window focus back by hand, and then the field that had
// it. This wraps the three dialogs once, at startup, so every call in the app is
// covered and none has to remember to.

type Dialogs = {
  alert: (message?: unknown) => void
  confirm: (message?: string) => boolean
  prompt: (message?: string, value?: string) => string | null
}

/** Restore focus once the dialog has closed: the window first (the main
 *  process blurs and refocuses it), then the element that had it. */
function restore(field: Element | null): void {
  const back = (): void => {
    if (field instanceof HTMLElement && field.isConnected) field.focus()
  }
  const ask = window.studio?.refocus
  if (ask) void ask().then(() => setTimeout(back, 0), back)
  else setTimeout(back, 0)
}

export function installNativeDialogFocusFix(target: Window = window): void {
  const native: Dialogs = {
    alert: target.alert.bind(target),
    confirm: target.confirm.bind(target),
    prompt: target.prompt.bind(target)
  }
  const wrap = <A extends unknown[], R>(fn: (...a: A) => R) => (...args: A): R => {
    const field = document.activeElement
    try {
      return fn(...args)
    } finally {
      restore(field)
    }
  }
  target.alert = wrap(native.alert)
  target.confirm = wrap(native.confirm)
  target.prompt = wrap(native.prompt)
}
