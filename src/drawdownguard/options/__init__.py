"""Black-Scholes: the prices and greeks every other module quotes from.

Called `optimizer` until 2026-08-27, when the convex program it was named for
went with the options wheel. What is left is arithmetic -- one implementation
of a price, a delta, a vega and an implied volatility, shared by the chain
adapter, the remedy solver and the risk gate so that no two of them can quietly
disagree about what a contract is worth.
"""
