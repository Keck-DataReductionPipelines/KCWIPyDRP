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
        1,
        'Interactive level'
    )
    SAVEINTIMS = _config.ConfigItem(
        False,
        'Save intermediate images'
    )
    PLOTPAUSE = _config.ConfigItem(
        1,
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
        7,
        'Minimum number of biases'
    )
    MINIMUM_NUMBER_OF_DARKS = _config.ConfigItem(
        3,
        'Minimum number of darks'
    )
    MINIMUM_NUMBER_OF_FLATS = _config.ConfigItem(
        6,
        'Minimum number of flats'
    )
    TAPERFRAC = _config.ConfigItem(
        0.2,
        'Taper fraction for atlas cross-correlation'
    )
    PIXSCALE = _config.ConfigItem(
        0.00004048,
        'Degrees per unbinned pixel'
    )
    SLICESCALE = _config.ConfigItem(
        0.00037718,
        'Degrees per Large slicer slice'
    )
    ROTOFF = _config.ConfigItem(
        0.0,
        'Rotator offset'
    )


KcwiConf = Conf()
