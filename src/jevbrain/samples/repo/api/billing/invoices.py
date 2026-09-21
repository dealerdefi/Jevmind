def invoice(customer, period):
    lines = usage(customer, period)
    return Invoice(customer, sum(l.amount for l in lines))
