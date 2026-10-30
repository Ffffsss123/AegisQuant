"""Declared securities execution scope; product slots are not trading permission.

FUTURE in simulation_slots is an accounting category only. Research or
reserved slots never confer securities execution capability.
"""
import json


def market_capabilities():
    return {
        'system': 'AegisQuant securities',
        'live_execution_enabled': False,
        'live_stock_execution_supported': True,
        'live_requires_explicit_enable': True,
        'markets': [
            {'market': 'US stocks', 'scope': 'Simulation execution and reconciliation',
             'category': 'US_STOCK', 'simulation_execution': True, 'live_execution_supported': True},
            {'market': 'Hong Kong stocks', 'scope': 'Simulation execution and reconciliation',
             'category': 'HK_STOCK', 'simulation_execution': True, 'live_execution_supported': True},
            {'market': 'US options', 'scope': 'Research and explicit simulation transport acceptance',
             'category': 'OPTION', 'simulation_execution': True},
            {'market': 'Futures', 'scope': 'Research only; execution and budget unverified',
             'category': 'FUTURE', 'simulation_execution': False},
        ],
        'note': 'Capabilities describe code paths, not completed execution acceptance or profitability.',
    }


if __name__ == '__main__':
    print(json.dumps(market_capabilities(), indent=2))
