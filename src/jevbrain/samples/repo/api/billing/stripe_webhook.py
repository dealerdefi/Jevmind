def on_event(evt):
    if evt.type == "invoice.paid":
        mark_paid(evt.data.id)
