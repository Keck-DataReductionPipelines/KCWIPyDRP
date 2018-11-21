# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
This packages contains the KCWI specific primitives and recipes
"""
from . import recipes

from astropy import config as _config
class Conf(_config.ConfigNamespace):
    """
    Configuration parameters for KCWIPyDRP.
    """
    CRZAP = _config.ConfigItem(
        True,
        'Perform cosmic ray rejection'
    )
    INTER = _config.ConfigItem(
        False,
        'Interactive operation'
    )
    PLOTPAUSE = _config.ConfigItem(
        0.5,
        'Pause length between plots in seconds'
    )
    MINOSCANPIX = _config.ConfigItem(
        75,
        'Minimum number of pixels for overscan'
    )
    OSCANBUF = _config.ConfigItem(
        20,
        'Pixel buffer to exclude at edges of overscan'
    )
    MINIMUM_NUMBER_OF_BIASES = _config.ConfigItem(
        5,
        'Minimum number of biases'
    )


KcwiConf = Conf()