from ..core import CcdPrimitives
from ..core import ImgmathPrimitives
from ..core import ProctabPrimitives
from ..core import DevelopmentPrimitives
import os
from .. import conf
from . import KcwiConf

import numpy as np
import scipy as sp
import scipy.interpolate as interp
from scipy.signal import find_peaks
from scipy.signal.windows import boxcar
from scipy import signal
from scipy.ndimage import gaussian_filter1d
from scipy.interpolate import interpolate
from scipy.optimize import curve_fit
from scipy.stats import sigmaclip, mode
from skimage import transform as tf
from astropy.table import Table
import astropy.io.fits as pf
import matplotlib.pyplot as pl
import time
import math

from astropy.nddata import VarianceUncertainty

import pkg_resources

import KeckDRP

################
# ccdproc usage
# import ccdproc
################


def pascal_shift(coef=None, x0=None):
    """Shift coefficients to a new reference value (X0)

    This should probably go somewhere else, but will be needed here.
    """
    if not coef:
        print("Error, no coefficients for pascal_shift.")
        return None
    if not x0:
        print("Warning, no reference value (x0) supplied")
        return coef
    if len(coef) == 7:
        usecoeff = list(reversed(coef))
        fincoeff = [0.] * 7
    else:
        if len(coef) > 7:
            print("Warning - this routine only handles up to 7 coefficients.")
            usecoeff = list(reversed(coef[0:7]))
            fincoeff = [0.] * len(coef)
        else:
            usecoeff = [0.] * 7
            fincoeff = usecoeff
            for ic, c in enumerate(coef):
                usecoeff[len(coef)-(ic+1)] = coef[ic]
    # get reference values
    x01 = x0
    x02 = x0**2
    x03 = x0**3
    x04 = x0**4
    x05 = x0**5
    x06 = x0**6
    # use Pascal's Triangle to shift coefficients
    fincoeff[0] = usecoeff[0] - usecoeff[1] * x01 + usecoeff[2] * x02 \
        - usecoeff[3] * x03 + usecoeff[4] * x04 - usecoeff[5] * x05 \
        + usecoeff[6] * x06

    fincoeff[1] = usecoeff[1] - 2.0 * usecoeff[2] * x01 \
        + 3.0 * usecoeff[3] * x02 - 4.0 * usecoeff[4] * x03 \
        + 5.0 * usecoeff[5] * x04 - 6.0 * usecoeff[6] * x05

    fincoeff[2] = usecoeff[2] - 3.0 * usecoeff[3] * x01 \
        + 6.0 * usecoeff[4] * x02 - 10.0 * usecoeff[5] * x03 \
        + 15.0 * usecoeff[6] * x04

    fincoeff[3] = usecoeff[3] - 4.0 * usecoeff[4] * x01 \
        + 10.0 * usecoeff[5] * x02 - 20.0 * usecoeff[6] * x03

    fincoeff[4] = usecoeff[4] - 5.0 * usecoeff[5] * x01 \
        + 15.0 * usecoeff[6] * x02

    fincoeff[5] = usecoeff[5] - 6.0 * usecoeff[6] * x01

    fincoeff[6] = usecoeff[6]
    # Trim if needed
    if len(coef) < 7:
        fincoeff = fincoeff[0:len(coef)]
    # Reverse for python
    return list(reversed(fincoeff))


def gaus(x, a, mu, sigma):
    """Gaussian fitting function"""
    return a * np.exp(-(x - mu) ** 2 / (2. * sigma ** 2))


def get_line_window(y, c, thresh=0., verbose=False):
    """Find a window that includes the fwhm of the line"""
    nx = len(y)
    # check edges
    if c < 2 or c > nx - 2:
        if verbose:
            print("input center too close to edge")
        return None, None, 0
    # get initial values
    x0 = c - 2
    x1 = c + 2
    mx = np.nanmax(y[x0:x1+1])
    count = 5
    # check low side
    if x0 - 1 < 0:
        if verbose:
            print("max check: low edge hit")
        return None, None, 0
    while y[x0-1] > mx:
        x0 -= 1
        count += 1
        if x0 - 1 < 0:
            if verbose:
                print("Max check: low edge hit")
            return None, None, 0

    # check high side
    if x1 + 1 > nx:
        if verbose:
            print("max check: high edge hit")
        return None, None, 0
    while y[x1+1] > mx:
        x1 += 1
        count += 1
        if x1 + 1 > nx:
            if verbose:
                print("Max check: high edge hit")
            return None, None, 0
    # adjust starting window to center on max
    cmx = x0 + y[x0:x1+1].argmax()
    x0 = cmx - 2
    x1 = cmx + 2
    mx = np.nanmax(y[x0:x1 + 1])
    # make sure max is high enough
    if mx < thresh:
        return None, None, 0
    #
    # expand until we get to half max
    hmx = mx * 0.5
    #
    # Low index side
    prev = mx
    while y[x0] > hmx:
        if y[x0] > mx or x0 <= 0 or y[x0] > prev:
            if verbose:
                if y[x0] > mx:
                    print("hafmax check: low index err - missed max")
                if x0 <= 0:
                    print("hafmax check: low index err - at edge")
                if y[x0] > prev:
                    print("hafmax check: low index err - wiggly")
            return None, None, 0
        prev = y[x0]
        x0 -= 1
        count += 1
    # High index side
    prev = mx
    while y[x1] > hmx:
        if y[x1] > mx or x1 >= nx or y[x1] > prev:
            if verbose:
                if y[x1] > mx:
                    print("hafmax check: high index err - missed max")
                if x1 >= nx:
                    print("hafmax check: high index err - at edge")
                if y[x1] > prev:
                    print("hafmax check: high index err - wiggly")
            return None, None, 0
        prev = y[x1]
        x1 += 1
        count += 1

    return x0, x1, count


class KcwiPrimitives(CcdPrimitives, ImgmathPrimitives,
                     ProctabPrimitives, DevelopmentPrimitives):

    def __init__(self):
        # KCWI constants
        self.NBARS = 120    # number of bars in continuum bars images
        self.REFBAR = 57    # bar number of reference bar
        self.PIX = 0.0150   # pixel size in mm
        self.FCAM = 305.0   # focal length of camera in mm
        self.GAMMA = 4.0    # mean out-of-plane angle for diffraction (deg)

        self.midrow = None          # middle row
        self.midcntr = None         # middle centroids
        self.win = None             # sample window
        self.arcs = None            # extracted arcs
        self.baroffs = None         # pixel offsets relative to ref bar
        self.prelim_disp = None     # calculated dispersion
        self.xvals = None           # pixel values centered on the middle
        self.x0 = None              # middle pixel
        self.reflux = None          # Atlas spectrum
        self.refwave = None         # Altas wavelengths
        self.refdisp = None         # Atlas dispersion
        self.minrow = None          # Lower limit for central fit (px)
        self.maxrow = None          # Upper limit for central fit (px)
        self.offset_wave = None     # atlas-arc offset in Angstroms
        self.offset_pix = None      # atlas-arc offset in pixels
        self.centcoeff = []         # Coeffs for central fit of each bar
        self.twkcoeff = []          # Coeffs pascal shifted
        self.fincoeff = []          # Final wavelength solution coeffs
        self.readnoise = None       # readnoise (e-)
        self.atrespix = None        # atlas matched resolution in atlas px
        self.atminrow = None        # atlas minimum row
        self.atmaxrow = None        # atlas maximum row
        self.atminwave = None       # atlas minimum wavelength (A)
        self.atmaxwave = None       # atlas maximum wavelength (A)
        self.at_wave = None         # atlas wavelength list
        self.arc_xpos = None        # arc ref bar line x position list
        self.arc_wave = None        # arc ref var line wavelength list
        super(KcwiPrimitives, self).__init__()

    @staticmethod
    def kcwi_plot_setup():
        if KcwiConf.INTER >= 1:
            pl.ion()
            fig = pl.figure(num=0, figsize=(17, 6))
            fig.canvas.set_window_title('KCWI DRP')

    def write_image(self, suffix=None):
        if suffix is not None:
            origfn = self.frame.header['OFNAME']
            if 'BUNIT' in self.frame.header:
                self.frame.unit = self.frame.header['BUNIT']
            outfn = os.path.join(conf.REDUXDIR,
                                 origfn.split('.')[0]+'_'+suffix+'.fits')
            if not conf.OVERWRITE and os.path.exists(outfn):
                self.log.error("output file exists: %s" % outfn)
            else:
                self.frame.write(outfn, overwrite=conf.OVERWRITE)
                self.log.info("output file: %s" % outfn)

    def write_table(self, table=None, suffix='table', names=None,
                    comment=None, keywords=None):
        if suffix is not None and table is not None:
            origfn = self.frame.header['OFNAME']
            outfn = os.path.join(conf.REDUXDIR,
                                 origfn.split('.')[0]+'_'+suffix+'.fits')
            if not conf.OVERWRITE and os.path.exists(outfn):
                self.log.error("output file exists: %s" % outfn)
            else:
                t = Table(table, names=names)
                t.meta['OFNAME'] = origfn
                if comment:
                    t.meta['COMMENT'] = comment
                if keywords:
                    for k, v in keywords.items():
                        t.meta[k] = v
                t.write(outfn, format='fits')
                self.log.info("output file: %s" % outfn)

    def read_table(self, tab=None, indir=None, suffix=None):
        # Set up return table
        retab = None
        # Construct table file name
        if tab is not None:
            flist = tab['OFNAME']
            if indir is None:
                pref = '.'
            else:
                pref = indir

            if suffix is None:
                suff = '.fits'
            else:
                suff = '_' + suffix + '.fits'
            for f in flist:
                infile = os.path.join(pref, f.split('.')[0] + suff)
                self.log.info("reading table: %s" % infile)
                retab = Table.read(infile, format='fits')
        else:
            self.log.error("No table to read")
        return retab

    def write_geom(self, suffix=None):
        if suffix is not None:
            origfn = self.frame.header['OFNAME']
            outfn = os.path.join(conf.REDUXDIR,
                                 origfn.split('.')[0]+'_'+suffix+'.fits')
            if not conf.OVERWRITE and os.path.exists(outfn):
                self.log.error("output file exists: %s" % outfn)
            else:
                # geometry writer goes here
                self.log.info("output file: %s" % outfn)

    def subtract_bias(self):
        tab = self.n_proctab(target_type='MBIAS', nearest=True)
        self.log.info("%d master bias frames found" % len(tab))
        if len(tab) > 0:
            self.img_subtract(tab, suffix='master_bias', indir='redux',
                              keylog='MBFILE')
            self.frame.header['BIASSUB'] = (True,
                                            self.keyword_comments['BIASSUB'])
            logstr = self.subtract_bias.__module__ + "." + \
                     self.subtract_bias.__qualname__
            self.frame.header['HISTORY'] = logstr
            self.log.info(self.subtract_bias.__qualname__)
        else:
            self.frame.header['BIASSUB'] = (False,
                                            self.keyword_comments['BIASSUB'])
            self.log.warn('No Master Bias frame found. NO BIAS SUBTRACTION')

    def create_unc(self):
        """Assumes units of image are electron"""
        # start with Poisson noise
        self.frame.uncertainty = VarianceUncertainty(
            self.frame.data, unit='electron^2', copy=True)
        # add readnoise, if known
        if self.readnoise:
            for ia in range(self.frame.namps()):
                sec, rfor = self.parse_imsec(
                    section_key='ATSEC%d' % (ia + 1))
                self.frame.uncertainty.array[
                 sec[0]:(sec[1]+1), sec[2]:(sec[3]+1)] += self.readnoise[ia]
                self.frame.header['BIASRN%d' % (ia + 1)] = self.readnoise[ia]
        else:
            self.log.warn("Readnoise undefined, uncertainty is Poisson only")
        # document variance image creation
        self.frame.header['UNCVAR'] = (True, "has variance image been created?")
        logstr = self.create_unc.__module__ + "." + \
                 self.create_unc.__qualname__
        self.frame.header['HISTORY'] = logstr
        self.log.info(self.create_unc.__qualname__)

    def subtract_dark(self):
        tab = self.n_proctab(target_type='MDARK', nearest=True)
        self.log.info("%d master dark frames found" % len(tab))
        if len(tab) > 0:
            self.img_subtract(tab, suffix='master_dark', indir='redux',
                              keylog='MDFILE')
            self.frame.header['DARKSUB'] = (True,
                                            self.keyword_comments['DARKSUB'])
            logstr = self.subtract_dark.__module__ + "." + \
                     self.subtract_dark.__qualname__
            self.frame.header['HISTORY'] = logstr
            self.log.info(self.subtract_dark.__qualname__)
        else:
            self.frame.header['DARKSUB'] = (False,
                                            self.keyword_comments['DARKSUB'])
            self.log.warn('No Master Dark frame found. NO DARK SUBTRACTION')

    def fit_flat(self):
        tab = self.n_proctab(target_type='FLAT', nearest=True)
        self.log.info("%d flat stacks found" % len(tab))
        # disable this for now
        if len(tab) > 10:
            idl_reference_procedure = self.get_idl_counterpart(
                target_type='CONTBARS')
            wavemap = self.read_idl_copy(idl_reference_procedure,
                                         suffix='wavemap')
            slicemap = self.read_idl_copy(idl_reference_procedure,
                                          suffix='slicemap')
            posmap = self.read_idl_copy(idl_reference_procedure,
                                        suffix='posmap')
            if posmap is None:
                self.log.warn(
                    "No idl reference file found. Stacking is impossible")
                return
            newflat = self.frame
            blueslice = 12
            blueleft = 30
            blueright = 40
            # p_order = 7
            qblue = np.where((slicemap.data == blueslice) &
                             (posmap.data >= blueleft) &
                             (posmap.data <= blueright))
            xfb = wavemap.data[qblue]
            yfb = newflat.data[qblue]
            s = np.argsort(xfb)
            xfb = xfb[s]
            yfb = yfb[s]
            # invar = 1 / (1 + np.abs(yfb))
            n = 100
            bkpt = np.min(wavemap.data[qblue]) + np.arange(n + 1) * \
                (np.max(wavemap.data[qblue]) - np.min(wavemap.data[qblue])) / n
            #          bkpty = interp.griddata(xfb, yfb, bkpt, method = 'cubic')
            bkpty = interp.griddata(xfb, yfb, bkpt)
            t, c, k = interp.splrep(bkpt, bkpty, k=3)
            #            flat_fit_coeffs = np.polyfit(bkpt, bkpty, p_order)
            #            t, c, k = interp.splrep(bkpt, bkpty, k=p_order)
            #            flat_fit = np.polyval(flat_fit_coeffs, bkpt)
            spline = interp.BSpline(t, c, k, extrapolate=False)
            # plot data and fit
            pl.ion()
            pl.plot(xfb, yfb)
            pl.plot(bkpt, spline(bkpt))
            pl.xlabel("angstrom")
            pl.ylabel("counts")
            pl.pause(KcwiConf.PLOTPAUSE)
            pl.clf()
            time.sleep(15)
            self.fit_flat()  # CC
            self.update_proctab(suffix='master_flat', newtype='MFLAT')
            self.log.info("master flat produced")
        else:
            self.log.info("fit_flat")

    def bias_readnoise(self, tab=None, in_directory=None):
        if tab is not None:
            file_list = tab['OFNAME']
            image_numbers = tab['FRAMENO']

            if in_directory is None:
                prefix = '.'
            else:
                prefix = in_directory
            suffix = '.fits'
            # get second and third image in stack
            infil1 = os.path.join(prefix, file_list[1].split('.')[0] + suffix)
            bias1 = KeckDRP.KcwiCCD.read(infil1, unit='adu')
            bias1.data = bias1.data.astype(np.float64)
            infil2 = os.path.join(prefix, file_list[2].split('.')[0] + suffix)
            bias2 = KeckDRP.KcwiCCD.read(infil2, unit='adu')
            bias2.data = bias2.data.astype(np.float64)
            namps = bias1.header['NVIDINP']
            for ia in range(namps):
                # get gain
                gain = bias1.header['GAIN%d' % (ia + 1)]
                # get amp section
                sec, rfor = self.parse_imsec(
                    section_key='DSEC%d' % (ia + 1))
                diff = bias1.data[sec[0]:(sec[1]+1), sec[2]:(sec[3]+1)] - \
                    bias2.data[sec[0]:(sec[1]+1), sec[2]:(sec[3]+1)]
                diff = np.reshape(diff, diff.shape[0]*diff.shape[1]) * \
                    gain / 1.414

                c, upp, low = sigmaclip(diff, low=3.5, high=3.5)
                bias_rn = c.std()
                self.log.info("Amp%d Read noise from bias in e-: %.3f" %
                              ((ia + 1), bias_rn))
                self.frame.header['BIASRN%d' % (ia + 1)] = \
                    (float("%.3f" % bias_rn), "RN in e- from bias")
                if self.frame.inter() >= 1:
                    if self.frame.inter() >= 2:
                        pl.ion()
                    else:
                        pl.ioff()
                    pl.clf()
                    pl.hist(c, bins=50, range=(-12, 12))
                    ylim = pl.gca().get_ylim()
                    pl.plot([c.mean(), c.mean()], ylim, 'g-.')
                    pl.plot([c.mean()-c.std(), c.mean()-c.std()], ylim, 'r-.')
                    pl.plot([c.mean()+c.std(), c.mean()+c.std()], ylim, 'r-.')
                    pl.xlabel("Bias1 - Bias2 (e-/sqrt(2)")
                    pl.ylabel("Density")
                    pl.gca().margins(0)
                    if self.frame.inter() >= 2:
                        input("Next? <cr>: ")
                    else:
                        pl.pause(self.frame.plotpause())

    def stack_biases(self):

        # get current group id
        if 'GROUPID' in self.frame.header:
            grpid = self.frame.header['GROUPID'].strip()
        else:
            grpid = None
        # how many biases do we have?
        combine_list = self.n_proctab(target_type='BIAS', target_group=grpid)
        self.log.info("number of biases = %d" % len(combine_list))
        # create master bias
        if len(combine_list) >= KcwiConf.MINIMUM_NUMBER_OF_BIASES:
            self.image_combine(combine_list, keylog='BIASLIST')
            self.bias_readnoise(combine_list)
            # output file and update proc table
            self.update_proctab(suffix='master_bias', newtype='MBIAS')
            self.write_image(suffix='master_bias')
            self.log.info("master bias produced")
        else:
            self.log.info('need %s biases to produce master' %
                          KcwiConf.MINIMUM_NUMBER_OF_BIASES)
        self.write_proctab()

    def stack_darks(self):

        # get current group id
        if 'GROUPID' in self.frame.header:
            grpid = self.frame.header['GROUPID'].strip()
        else:
            grpid = None

        # how many darks do we have?
        combine_list = self.n_proctab(target_type='DARK', target_group=grpid)
        self.log.info("number of darks = %d" % len(combine_list))
        # create master bias
        if len(combine_list) >= KcwiConf.MINIMUM_NUMBER_OF_DARKS:
            self.image_combine(combine_list, keylog='DARKLIST',
                               in_directory='redux', suffix='int')
            # output file and update proc table
            self.update_proctab(suffix='master_dark', newtype='MDARK')
            self.write_image(suffix='master_dark')
            self.log.info("master dark produced")
        else:
            self.log.info('need %s darks to produce master' %
                          KcwiConf.MINIMUM_NUMBER_OF_DARKS)
        self.write_proctab()

    def stack_internal_flats(self):
        # how many flats do we have?
        combine_list = self.n_proctab(target_type='FLATLAMP')
        self.log.info("number of flats = %d" % len(combine_list))
        # create master flat
        if len(combine_list) >= KcwiConf.MINIMUM_NUMBER_OF_FLATS:
            self.image_combine(combine_list, unit=None, suffix='int',
                               in_directory=conf.REDUXDIR, keylog='FLATLIST')
            # output file and update proc table
            self.update_proctab(suffix='flat_stack', newtype='FLAT')
            self.write_image(suffix='flat_stack')
            self.log.info("flat stack produced")

        else:
            self.log.info('need %s flats to produce master' %
                          KcwiConf.MINIMUM_NUMBER_OF_FLATS)
        self.write_proctab()

    def subtract_scattered_light(self):
        # keyword of record
        key = 'SCATSUB'
        if self.frame.nasmask():
            self.log.info("NAS Mask in: skipping scattered light subtraction.")
            self.frame.header[key] = (False, self.keyword_comments[key])
        else:

            # Get size of image
            siz = self.frame.data.shape
            # Get x range for scattered light
            x0 = int(siz[1] / 2 - 180 / self.frame.xbinsize())
            x1 = int(siz[1] / 2 + 180 / self.frame.xbinsize())
            # Get y limits
            y0 = 0
            # y1 = int(siz[0] / 2 - 1)
            # y2 = y1 + 1
            y3 = siz[0]
            # print("x limits: %d, %d, y limits: %d, %d" % (x0, x1, y0, y3))
            # Y data values
            yvals = np.nanmedian(self.frame.data[y0:y3, x0:x1], axis=1)
            # X data values
            xvals = np.arange(len(yvals), dtype=np.float)
            # Break points
            nbkpt = int(siz[1]/40.)
            bkpt = xvals[nbkpt:-nbkpt:nbkpt]
            # B-spline fit
            bspl = sp.interpolate.LSQUnivariateSpline(xvals, yvals, bkpt)
            if KcwiConf.INTER >= 1:
                # plot
                pl.ion()
                pl.plot(xvals, yvals, 'ro')
                legend = ["Scat", ]
                xx = np.linspace(0, max(xvals), len(yvals)*5)
                pl.plot(xx, bspl(xx), 'b-')
                legend.append("fit")
                pl.xlabel("y pixel")
                pl.ylabel("e-")
                pl.title("Scat Light img #%d" % (self.frame.header['FRAMENO']))
                pl.legend(legend)
                if KcwiConf.INTER >= 2:
                    input("Next? <cr>: ")
                else:
                    pl.pause(KcwiConf.PLOTPAUSE)
            # Scattered light vector
            scat = bspl(xvals)
            # Subtract scattered light
            self.log.info("Starting scattered light subtraction")
            for ix in range(0, siz[1]):
                self.frame.data[y0:y3, ix] = \
                    self.frame.data[y0:y3, ix] - scat
            self.frame.header[key] = (True, self.keyword_comments[key])

        logstr = self.subtract_scattered_light.__module__ + "." + \
                 self.subtract_scattered_light.__qualname__
        self.frame.header['HISTORY'] = logstr
        self.log.info(self.subtract_scattered_light.__qualname__)

    def find_bars(self):
        self.log.info("Finding continuum bars")
        # Do we plot?
        if KcwiConf.INTER >= 1:
            do_plot = True
            pl.ion()
        else:
            do_plot = False
        # initialize
        midcntr = []
        # get image dimensions
        nx = self.frame.data.shape[1]
        ny = self.frame.data.shape[0]
        # get binning
        ybin = self.frame.ybinsize()
        win = int(10 / ybin)
        # select from center rows of image
        midy = int(ny / 2)
        midvec = np.median(self.frame.data[(midy-win):(midy+win+1), :], axis=0)
        # set threshold for peak finding
        midavg = np.average(midvec)
        self.log.info("peak threshold = %f" % midavg)
        # find peaks above threshold
        midpeaks, _ = find_peaks(midvec, height=midavg)
        # do we have the requisite number?
        if len(midpeaks) != self.NBARS:
            self.log.error("Did not find %d peaks: n peaks = %d"
                           % (self.NBARS, len(midpeaks)))
        else:
            self.log.info("found %d bars" % len(midpeaks))
            if do_plot:
                # plot the peak positions
                pl.plot(midvec, '-')
                pl.plot(midpeaks, midvec[midpeaks], 'rx')
                pl.plot([0, nx], [midavg, midavg], '--', color='grey')
                pl.xlabel("CCD X (px)")
                pl.ylabel("e-")
                pl.title("Img %d, Thresh = %.2f" %
                         (self.frame.header['FRAMENO'], midavg))
            # calculate the bar centroids
            for peak in midpeaks:
                xs = list(range(peak-win, peak+win+1))
                ys = midvec[xs] - np.nanmin(midvec[xs])
                xc = np.sum(xs*ys) / np.sum(ys)
                midcntr.append(xc)
                if do_plot:
                    pl.plot([xc, xc], [midavg, midvec[peak]], '-.',
                            color='grey')
            if do_plot:
                pl.plot(midcntr, midvec[midpeaks], 'gx')
                if KcwiConf.INTER >= 2:
                    input("next: ")
                else:
                    pl.pause(self.frame.plotpause())
            self.log.info("Found middle centroids for continuum bars")
        self.midcntr = midcntr
        self.midrow = midy
        self.win = win

    def trace_bars(self):
        self.log.info("Tracing continuum bars")
        if KcwiConf.INTER >= 1:
            do_plot = True
            pl.ion()
        else:
            do_plot = False
        if len(self.midcntr) < 1:
            self.log.error("No bars found")
        else:
            # initialize
            samp = int(80 / self.frame.ybinsize())
            win = self.win
            xi = []     # x input
            xo = []     # x output
            yi = []     # y input (and output)
            barid = []  # bar id number
            slid = []   # slice id number
            # loop over bars
            for barn, barx in enumerate(self.midcntr):
                # nearest pixel to bar center
                barxi = int(barx + 0.5)
                # print("bar number %d is at %.3f" % (barn, barx))
                # middle row data
                xi.append(barx)
                xo.append(barx)
                yi.append(self.midrow)
                barid.append(barn)
                slid.append(int(barn/5))
                # trace up
                samy = self.midrow + samp
                done = False
                while samy < (self.frame.data.shape[0] - win) and not done:
                    ys = np.median(
                        self.frame.data[(samy - win):(samy + win + 1),
                                        (barxi - win):(barxi + win + 1)],
                        axis=0)
                    ys = ys - np.nanmin(ys)
                    xs = list(range(barxi - win, barxi + win + 1))
                    xc = np.sum(xs * ys) / np.sum(ys)
                    if np.nanmax(ys) > 255:
                        xi.append(xc)
                        xo.append(barx)
                        yi.append(samy)
                        barid.append(barn)
                        slid.append(int(barn/5))
                    else:
                        done = True
                    samy += samp
                # trace down
                samy = self.midrow - samp
                done = False
                while samy >= win and not done:
                    ys = np.median(
                        self.frame.data[(samy - win):(samy + win + 1),
                                        (barxi - win):(barxi + win + 1)],
                        axis=0)
                    ys = ys - np.nanmin(ys)
                    xs = list(range(barxi - win, barxi + win + 1))
                    xc = np.sum(xs * ys) / np.sum(ys)
                    if np.nanmax(ys) > 255:
                        xi.append(xc)
                        xo.append(barx)
                        yi.append(samy)
                        barid.append(barn)
                        slid.append(int(barn / 5))
                    else:
                        done = True
                    # disable for now
                    samy -= samp
            # end loop over bars
            # create source and destination coords
            yo = yi
            dst = np.column_stack((xi, yi))
            src = np.column_stack((xo, yo))
            if do_plot:
                # plot them
                pl.clf()
                # pl.ioff()
                pl.plot(xi, yi, 'x', ms=0.5)
                pl.plot(self.midcntr, [self.midrow]*120, 'x', color='red')
                pl.xlabel("CCD X (px)")
                pl.ylabel("CCD Y (px)")
                pl.title("Img %d" % self.frame.header['FRAMENO'])
                if KcwiConf.INTER >= 2:
                    pl.show()
                    input("next: ")
                else:
                    pl.pause(self.frame.plotpause())
            self.write_table(table=[src, dst, barid, slid],
                             names=('src', 'dst', 'barid', 'slid'),
                             suffix='trace',
                             comment=['Source and destination fiducial points',
                                      'Derived from KCWI continuum bars images',
                                      'For defining spatial transformation'],
                             keywords={'MIDROW': self.midrow,
                                       'WINDOW': self.win})
            if self.frame.saveintims():
                # fit transform
                self.log.info("Fitting spatial control points")
                tform = tf.estimate_transform('polynomial', src, dst, order=3)
                self.log.info("Transforming bars image")
                warped = tf.warp(self.frame.data, tform)
                # write out warped image
                self.frame.data = warped
                self.write_image(suffix='warped')
                self.log.info("Transformed bars produced")

    def extract_arcs(self):
        self.log.info("Extracting arc spectra")
        # Find  and read control points from continuum bars
        tab = self.n_proctab(target_type='CONTBARS', nearest=True)
        self.log.info("%d continuum bars frames found" % len(tab))
        trace = self.read_table(tab=tab, indir='redux', suffix='trace')
        src = trace['src']  # source control points
        dst = trace['dst']  # destination control points
        barid = trace['barid']
        slid = trace['slid']
        # Get other items
        midrow = trace.meta['MIDROW']
        win = trace.meta['WINDOW']

        self.log.info("Fitting spatial control points")
        tform = tf.estimate_transform('polynomial', src, dst, order=3)

        self.log.info("Transforming arc image")
        warped = tf.warp(self.frame.data, tform)
        # Write warped arcs if requested
        if self.frame.saveintims():
            # write out warped image
            self.frame.data = warped
            self.write_image(suffix='warped')
            self.log.info("Transformed arcs produced")
        # extract arcs
        self.log.info("Extracting arcs")
        arcs = []
        for xyi, xy in enumerate(src):
            if xy[1] == midrow:
                xi = int(xy[0]+0.5)
                arc = np.median(
                    warped[:, (xi - win):(xi + win + 1)], axis=1)
                arc = arc - np.nanmin(arc[100:-100])    # avoid ends
                arcs.append(arc)
        # Did we get the correct number of arcs?
        if len(arcs) == self.NBARS:
            self.log.info("Extracted %d arcs" % len(arcs))
            self.arcs = arcs
        else:
            self.log.error("Did not extract %d arcs, extracted %d" %
                           (self.NBARS, len(arcs)))

    def arc_offsets(self):
        self.log.info("Finding inter-bar offsets")
        if self.arcs is not None:
            # Do we plot?
            if KcwiConf.INTER >= 2:
                do_plot = True
                pl.ion()
            else:
                do_plot = False
            # Compare with reference arc
            refarc = self.arcs[self.REFBAR][:]
            # number of cross-correlation samples (avoiding ends)
            nsamp = len(refarc[10:-10])
            # possible offsets
            offar = np.arange(1-nsamp, nsamp)
            # Collect offsets
            offsets = []
            for na, arc in enumerate(self.arcs):
                # Cross-correlate, avoiding junk on the ends
                xcorr = np.correlate(refarc[10:-10], arc[10:-10], mode='full')
                # Calculate offset
                offset = offar[xcorr.argmax()]
                offsets.append(offset)
                self.log.info("Arc %d Slice %d XCorr shift = %d" %
                              (na, int(na/5), offset))
                # display if requested
                if do_plot:
                    pl.clf()
                    pl.plot(refarc, color='green')
                    pl.plot(np.roll(arc, offset), color='red')
                    pl.ylim(bottom=0.)
                    pl.xlabel("CCD y (px)")
                    pl.ylabel("e-")
                    pl.title(self.frame.plotlabel() +
                             " Arc %d Slice %d XCorr, Shift = %d" %
                             (na, int(na/5), offset))
                    pl.show()
                    q = input("<cr> - Next, q to quit: ")
                    if 'Q' in q.upper():
                        do_plot = False
            self.baroffs = offsets
            if self.frame.inter() >= 1:
                if self.frame.inter() >= 2:
                    pl.ion()
                else:
                    pl.ioff()
                pl.clf()
                pl.plot(offsets, 'd')
                ylim = pl.gca().get_ylim()
                for ix in range(1, 24):
                    sx = ix * 5 - 0.5
                    pl.plot([sx, sx], ylim, 'k-.')
                pl.plot([-1, 120], [0., 0.], 'k--')
                pl.xlabel("Bar #")
                pl.ylabel("Offset (px)")
                pl.title(self.frame.plotlabel())
                pl.gca().margins(0)
                if self.frame.inter() >= 2:
                    input("Next? <cr>: ")
                else:
                    pl.pause(self.frame.plotpause())
        else:
            self.log.error("No extracted arcs found")

    def calc_prelim_disp(self):
        # get binning
        ybin = self.frame.ybinsize()
        # 0 - compute alpha
        prelim_alpha = self.frame.grangle() - 13.0 - self.frame.adjang()
        # 1 - compute preliminary angle of diffraction
        prelim_beta = self.frame.camang() - prelim_alpha
        # 2 - compute preliminary dispersion
        prelim_disp = math.cos(prelim_beta/math.degrees(1.)) / \
            self.frame.rho() / self.FCAM * (self.PIX*ybin) * 1.e4
        prelim_disp *= math.cos(self.GAMMA/math.degrees(1.))
        self.log.info("Initial alpha, beta (deg): %.3f, %.3f" %
                      (prelim_alpha, prelim_beta))
        self.log.info("Initial calculated dispersion (A/binned pix): %.3f" %
                      prelim_disp)
        self.prelim_disp = prelim_disp

    def read_atlas(self):
        # What lamp are we using?
        lamp = self.frame.illum()
        atpath = os.path.join(pkg_resources.resource_filename(
            'KeckDRP.KCWI', 'data/'), "%s.fits" % lamp.lower())
        # Does the atlas file exist?
        if os.path.exists(atpath):
            self.log.info("Reading atlas spectrum in: %s" % atpath)
        else:
            self.log.error("Atlas spectrum not found for %s" % atpath)
        # Read the atlas
        ff = pf.open(atpath)
        reflux = ff[0].data
        refdisp = ff[0].header['CDELT1']
        refwav = np.arange(0, len(reflux)) * refdisp + ff[0].header['CRVAL1']
        ff.close()
        # Convolve with appropriate Gaussian
        resolution = self.frame.resolution(refwave=self.frame.cwave())
        atrespix = resolution / refdisp
        self.log.info("Resolution = %.3f Ang, or %.2f Atlas px" % (resolution,
                                                                   atrespix))
        reflux = gaussian_filter1d(reflux, atrespix/2.354)   # convert to sigma
        # Observed arc spectrum
        obsarc = self.arcs[self.REFBAR]
        # Preliminary wavelength solution
        xvals = np.arange(0, len(obsarc)) - int(len(obsarc)/2)
        obswav = xvals * self.prelim_disp + self.frame.cwave()
        # Get central third
        minow = int(len(obsarc)/3)
        maxow = int(2.*len(obsarc)/3)
        # Unless we are low dispersion, then get central 3 5ths
        if 'BL' in self.frame.grating() or 'RL' in self.frame.grating():
            minow = int(len(obsarc)/5)
            maxow = int(4.*len(obsarc)/5)
        minwav = obswav[minow]
        maxwav = obswav[maxow]
        # Get corresponding ref range
        minrw = [i for i, v in enumerate(refwav) if v >= minwav][0]
        maxrw = [i for i, v in enumerate(refwav) if v <= maxwav][-1]
        # Subsample for cross-correlation
        cc_obsarc = obsarc[minow:maxow].copy()
        cc_obswav = obswav[minow:maxow]
        cc_reflux = reflux[minrw:maxrw].copy()
        cc_refwav = refwav[minrw:maxrw]
        # Resample onto reference wavelength scale
        obsint = interpolate.interp1d(cc_obswav, cc_obsarc, kind='cubic',
                                      bounds_error=False,
                                      fill_value='extrapolate'
                                      )
        cc_obsarc = obsint(cc_refwav)
        # Apply cosign bell taper to both
        cc_obsarc *= signal.windows.tukey(len(cc_obsarc),
                                          alpha=self.frame.taperfrac())
        cc_reflux *= signal.windows.tukey(len(cc_reflux),
                                          alpha=self.frame.taperfrac())
        nsamp = len(cc_refwav)
        offar = np.arange(1 - nsamp, nsamp)
        # Cross-correlate
        xcorr = np.correlate(cc_obsarc, cc_reflux, mode='full')
        # Get central region
        x0c = int(len(xcorr)/3)
        x1c = int(2*(len(xcorr)/3))
        xcorr_central = xcorr[x0c:x1c]
        offar_central = offar[x0c:x1c]
        # Calculate offset
        offset_pix = offar_central[xcorr_central.argmax()]
        offset_wav = offset_pix * refdisp
        self.log.info("Initial arc-atlas offset (px, Ang): %d, %.1f" %
                      (offset_pix, offset_wav))
        if self.frame.inter() >= 1:
            if self.frame.inter() >= 2:
                pl.ion()
            else:
                pl.ioff()
            # Plot
            pl.clf()
            pl.plot(offar_central, xcorr_central)
            ylim = pl.gca().get_ylim()
            pl.plot([offset_pix, offset_pix], ylim, 'g-.')
            pl.xlabel("Offset(px)")
            pl.ylabel("X-corr")
            pl.title("Img # %d (%s), Offset = %d px" %
                     (self.frame.header['FRAMENO'], lamp, offset_pix))
            if self.frame.inter() >= 2:
                input("Next? <cr>: ")
            else:
                pl.pause(self.frame.plotpause())
            # Get central wavelength
            cwave = self.frame.cwave()
            # Set up offset tweaking
            q = 'test'
            while q:
                # Plot the two spectra
                pl.clf()
                pl.plot(obswav[minow:maxow] - offset_wav,
                        obsarc[minow:maxow]/np.nanmax(obsarc[minow:maxow]),
                        '-', label="ref bar (%d)" % self.REFBAR)
                pl.plot(refwav[minrw:maxrw],
                        reflux[minrw:maxrw]/np.nanmax(reflux[minrw:maxrw]),
                        'r-', label="Atlas")
                pl.xlim(np.nanmin(obswav[minow:maxow]),
                        np.nanmax(obswav[minow:maxow]))
                ylim = pl.gca().get_ylim()
                pl.plot([cwave, cwave], ylim, 'g-.', label="CWAVE")
                pl.xlabel("Wave(A)")
                pl.ylabel("Rel. Flux")
                pl.title("Img # %d (%s), Offset = %.1f Ang (%d px)" %
                         (self.frame.header['FRAMENO'], lamp,
                          offset_wav, offset_pix))
                pl.legend()
                if self.frame.inter() >= 2:
                    q = input("Enter: <cr> - next, new offset (px): ")
                    if q:
                        try:
                            offset_pix = int(q)
                            offset_wav = offset_pix * refdisp
                        except ValueError:
                            print("Try again")
                else:
                    pl.pause(self.frame.plotpause())
                    q = None
            self.log.info("Final   arc-atlas offset (px, Ang): %d, %.1f" %
                          (offset_pix, offset_wav))
        # Store atlas spectrum
        self.reflux = reflux
        self.refwave = refwav
        self.atrespix = atrespix
        # Store offsets
        self.offset_pix = offset_pix
        self.offset_wave = offset_wav
        # Store reference dispersion
        self.refdisp = refdisp
        # Store central limits
        self.minrow = minow
        self.maxrow = maxow
        # Store x values
        self.xvals = xvals
        self.x0 = int(len(obsarc)/2)

    def fit_center(self):
        """ Fit central region

        At this point we have the offsets between bars and the approximate
        offset from the reference bar to the atlas spectrum and the approximate
        dispersion.
        """
        self.log.info("Finding wavelength solution for central region")
        # Are we interactive?
        if KcwiConf.INTER >= 2:
            do_inter = True
            pl.ion()
        else:
            do_inter = False
        # y binning
        ybin = self.frame.ybinsize()
        # let's populate the 0 points vector
        p0 = self.frame.cwave() + np.array(self.baroffs) * self.prelim_disp \
            - self.offset_wave
        # next we are going to brute-force scan around the preliminary
        # dispersion for a better solution. We will wander 5% away from it.
        max_ddisp = 0.05    # fraction
        # we will try nn values
        nn = (int(max_ddisp*abs(self.prelim_disp)/self.refdisp*(
                self.maxrow-self.minrow)/3.0))
        if nn < 10:
            nn = 10
        if nn > 25:
            nn = 25
        self.log.info("N disp. samples: %d" % nn)
        # dispersions to try
        disps = self.prelim_disp * (1.0 + max_ddisp *
                                    (np.arange(0, nn+1) - nn/2.) * 2.0 / nn)
        # containers for bar-specific values
        bardisp = []
        barshift = []
        centwave = []
        centdisp = []

        # values for central fit
        subxvals = self.xvals[self.minrow:self.maxrow]
        # loop over bars
        for b, bs in enumerate(self.arcs):
            # wavelength coefficients
            coeff = [0., 0., 0., 0., 0.]
            # container for maxima, shifts
            maxima = []
            shifts = []
            # get sub spectrum for this bar
            subspec = bs[self.minrow:self.maxrow]
            # now loop over dispersions
            for di, disp in enumerate(disps):
                # populate the coefficients
                coeff[4] = p0[b]
                coeff[3] = disp
                cosbeta = disp / (self.PIX*ybin) * self.frame.rho() * \
                    self.FCAM * 1.e-4
                if cosbeta > 1.:
                    cosbeta = 1.
                beta = math.acos(cosbeta)
                coeff[2] = -(self.PIX * ybin / self.FCAM) ** 2 * \
                    math.sin(beta) / 2. / self.frame.rho() * 1.e4
                coeff[1] = -(self.PIX * ybin / self.FCAM) ** 3 * \
                    math.cos(beta) / 6. / self.frame.rho() * 1.e4
                coeff[0] = (self.PIX * ybin / self.FCAM) ** 4 * \
                    math.sin(beta) / 24. / self.frame.rho() * 1.e4
                # what are the min and max wavelengths to consider?
                wl0 = np.polyval(coeff, self.xvals[self.minrow])
                wl1 = np.polyval(coeff, self.xvals[self.maxrow])
                minwvl = np.nanmin([wl0, wl1])
                maxwvl = np.nanmax([wl0, wl1])
                # where will we need to interpolate to cross-correlate?
                minrw = [i for i, v in enumerate(self.refwave)
                         if v >= minwvl][0]
                maxrw = [i for i, v in enumerate(self.refwave)
                         if v <= maxwvl][-1]
                subrefwvl = self.refwave[minrw:maxrw]
                # need a copy to avoid altering original
                subrefspec = self.reflux[minrw:maxrw].copy()
                # get bell cosine taper to avoid nasty edge effects
                tkwgt = signal.windows.tukey(len(subrefspec),
                                             alpha=self.frame.taperfrac())
                # apply taper to atlas spectrum
                subrefspec *= tkwgt
                # adjust wavelengths
                waves = np.polyval(coeff, subxvals)
                # interpolate the bar spectrum
                obsint = interpolate.interp1d(waves, subspec, kind='cubic',
                                              bounds_error=False,
                                              fill_value='extrapolate')
                intspec = obsint(subrefwvl)
                # apply taper to bar spectrum
                intspec *= tkwgt
                # get a label
                # cross correlate the interpolated spectrum with the atlas spec
                nsamp = len(subrefwvl)
                offar = np.arange(1 - nsamp, nsamp)
                # Cross-correlate
                xcorr = np.correlate(intspec, subrefspec, mode='full')
                # Get central region
                x0c = int(len(xcorr) / 3)
                x1c = int(2 * (len(xcorr) / 3))
                xcorr_central = xcorr[x0c:x1c]
                offar_central = offar[x0c:x1c]
                # Calculate offset
                maxima.append(xcorr_central[xcorr_central.argmax()])
                shifts.append(offar_central[xcorr_central.argmax()])
            # Get interpolations
            int_max = interpolate.interp1d(disps, maxima, kind='cubic',
                                           bounds_error=False,
                                           fill_value='extrapolate')
            int_shift = interpolate.interp1d(disps, shifts, kind='cubic',
                                             bounds_error=False,
                                             fill_value='extrapolate')
            xdisps = np.linspace(min(disps), max(disps), num=nn*100)
            # get peak values
            maxima_res = int_max(xdisps)
            shifts_res = int_shift(xdisps) * self.refdisp
            bardisp.append(xdisps[maxima_res.argmax()])
            barshift.append(shifts_res[maxima_res.argmax()])
            # update coeffs
            coeff[4] = p0[b] - barshift[-1]
            coeff[3] = bardisp[-1]
            cosbeta = coeff[3] / (self.PIX * ybin) * self.frame.rho() * \
                self.FCAM * 1.e-4
            if cosbeta > 1.:
                cosbeta = 1.
            beta = math.acos(cosbeta)
            coeff[2] = -(self.PIX * ybin / self.FCAM) ** 2 * \
                math.sin(beta) / 2. / self.frame.rho() * 1.e4
            coeff[1] = -(self.PIX * ybin / self.FCAM) ** 3 * \
                math.cos(beta) / 6. / self.frame.rho() * 1.e4
            coeff[0] = (self.PIX * ybin / self.FCAM) ** 4 * \
                math.sin(beta) / 24. / self.frame.rho() * 1.e4
            scoeff = pascal_shift(coeff, self.x0)
            self.log.info("Central Fit: Bar#, Cdisp, Coefs: "
                          "%3d  %.4f  %.2f  %.4f  %13.5e %13.5e" %
                          (b, bardisp[-1], scoeff[4], scoeff[3], scoeff[2],
                           scoeff[1]))
            # store central values
            centwave.append(coeff[4])
            centdisp.append(coeff[3])
            # Store results
            self.centcoeff.append(coeff)

            if self.frame.inter() >= 1:
                # plot maxima
                pl.clf()
                pl.plot(disps, maxima, 'r.', label='Data', ms=8)
                pl.plot(xdisps, int_max(xdisps), '-', label='Int')
                ylim = pl.gca().get_ylim()
                pl.plot([bardisp[-1], bardisp[-1]], ylim, 'g--', label='BrDsp')
                pl.plot([self.prelim_disp, self.prelim_disp], ylim, 'r-.',
                        label='InDsp')
                pl.xlabel("Central Dispersion (Ang/px)")
                pl.ylabel("X-Corr Peak Value")
                pl.title(self.frame.plotlabel() +
                         "Bar %d, Slice %d" % (b, int(b/5)))
                pl.legend()
                pl.gca().margins(0)
                if do_inter:
                    q = input("<cr> - Next, q to quit: ")
                    if 'Q' in q.upper():
                        do_inter = False
                        pl.ioff()
                else:
                    pl.pause(0.01)

        if self.frame.inter() >= 1:
            if self.frame.inter() >= 2:
                pl.ion()
            else:
                pl.ioff()
            # Plot results
            pl.clf()
            pl.plot(centwave, 'h')
            ylim = pl.gca().get_ylim()
            for ix in range(1, 24):
                sx = ix*5 - 0.5
                pl.plot([sx, sx], ylim, '-.', color='black')
            pl.xlim([-1, 120])
            pl.gca().margins(0)
            pl.xlabel("Bar #")
            pl.ylabel("Central Wavelength (A)")
            pl.title(self.frame.plotlabel())
            if self.frame.inter() >= 2:
                input("Next? <cr>: ")
            else:
                pl.pause(self.frame.plotpause())
            pl.clf()
            pl.plot(centdisp, 'h')
            ylim = pl.gca().get_ylim()
            for ix in range(1, 24):
                sx = ix * 5 - 0.5
                pl.plot([sx, sx], ylim, '-.', color='black')
            pl.xlim([-1, 120])
            pl.gca().margins(0)
            pl.xlabel("Bar #")
            pl.ylabel("Central Dispersion (A/px)")
            pl.title(self.frame.plotlabel())
            if self.frame.inter() >= 2:
                input("Next? <cr>: ")
            else:
                pl.pause(self.frame.plotpause())

    def get_atlas_lines(self):
        """Get relevant atlas line positions and wavelengths"""
        if KcwiConf.INTER >= 3:
            do_inter = True
            pl.ion()
        else:
            do_inter = False

        # get atlas wavelength range
        #
        # get pixel values (no longer centered in the middle)
        specsz = len(self.arcs[self.REFBAR])
        xvals = np.arange(0, specsz)
        # min, max rows
        minrow = 50
        maxrow = specsz-50
        # wavelength range
        mnwvs = []
        mxwvs = []
        # pascal shift coeffs
        twkcoeff = []
        for b in range(self.NBARS):
            twkcoeff.append(pascal_shift(self.centcoeff[b], self.x0))
            waves = np.polyval(twkcoeff[-1], xvals)
            mnwvs.append(np.min(waves))
            mxwvs.append(np.max(waves))
        self.twkcoeff = twkcoeff
        minwav = min(mnwvs) + 10.
        maxwav = max(mxwvs) - 10.
        # Get corresponding atlas range
        minrw = [i for i, v in enumerate(self.refwave) if v >= minwav][0]
        maxrw = [i for i, v in enumerate(self.refwave) if v <= maxwav][-1]
        self.log.info("Min, Max wave (A): %.2f, %.2f" % (minwav, maxwav))
        # store atlas ranges
        self.atminrow = minrw
        self.atmaxrow = maxrw
        self.atminwave = minwav
        self.atmaxwave = maxwav
        # get atlas sub spectrum
        atspec = self.reflux[minrw:maxrw]
        atwave = self.refwave[minrw:maxrw]
        # get reference bar spectrum
        subxvals = xvals[minrow:maxrow]
        subyvals = self.arcs[self.REFBAR][minrow:maxrow].copy()
        subwvals = np.polyval(twkcoeff[self.REFBAR], subxvals)
        # smooth subyvals
        win = boxcar(3)
        subyvals = sp.signal.convolve(subyvals, win, mode='same') / sum(win)
        # np.save("obspec", subyvals)
        # np.save("atspec", atspec)
        # find atlas peaks
        hgt = np.median(atspec)
        spmode = mode(np.round(atspec))
        self.log.info("Atlas spec median = %.3f, mode = %d" %
                      (hgt, spmode.mode[0]))
        wid = [self.atrespix * 0.5, self.atrespix * 3.0]
        dis = self.atrespix * 4.    # avoid blended lines
        self.log.info("Using distance of %.1f and width range of %.1f - %.1f" %
                      (dis, wid[0], wid[1]))
        peaks, props = sp.signal.find_peaks(atspec, distance=dis,
                                            width=wid)
        self.log.info("Found %d peaks" % len(peaks))

        # Fit gaussian to peaks
        sigs = []
        cent = []
        amps = []
        peks = []
        x0s = []
        x1s = []
        for ipk, pk in enumerate(peaks):
            x0 = int(props['left_ips'][ipk]+0.5) - 1
            x1 = int(props['right_ips'][ipk]+0.5) + 2
            yvec = atspec[x0:x1]
            xvec = atwave[x0:x1]
            try:
                fit, _ = curve_fit(gaus, xvec, yvec, p0=[100., atwave[pk], 1.])
                amps.append(fit[0])
                cent.append(fit[1])
                sigs.append(abs(fit[2]))
                peks.append(pk)
                x0s.append(x0)
                x1s.append(x1)
            except RuntimeError:
                self.log.info("Gaussian fit failed for peak %d" % ipk)
        # compile stats
        sig_clean, low, upp = sp.stats.sigmaclip(sigs, low=2., high=2.)
        self.log.info("Nclean, low, up = %d, %.3f, %.3f" % (len(sig_clean),
                                                            low, upp))
        mnsig = sig_clean.mean()
        stsig = sig_clean.std()
        self.log.info("<sig> = %.3f +- %.3f (px)" % (mnsig, stsig))

        at_wave = []
        at_amp = []
        rej_wave = []
        rej_amp = []
        nrej = 0
        for ipk, pk in enumerate(peks):
            # if mnsig + stsig > sigs[ipk] > mnsig - stsig*2.:
            # if sigs[ipk] < mnsig * 2.0:
            if low < sigs[ipk] < upp:
                at_amp.append(amps[ipk])
                x0 = x0s[ipk]
                x1 = x1s[ipk]
                yvec = atspec[x0:x1]
                xvec = atwave[x0:x1]
                # Get interpolation
                int_line = interpolate.interp1d(xvec, yvec, kind='cubic',
                                                bounds_error=False,
                                                fill_value='extrapolate')
                xplot = np.linspace(min(xvec), max(xvec), num=1000)
                # get peak value
                plt_line = int_line(xplot)
                peak = xplot[plt_line.argmax()]
                if abs(cent[ipk] - peak) > 2:
                    print("large peak - center offset, skip line at %.3f" %
                          cent[ipk])
                    rej_wave.append(cent[ipk])
                    rej_amp.append(amps[ipk])
                    continue
                at_wave.append(xplot[plt_line.argmax()])
                if do_inter:
                    pl.clf()
                    pl.plot(xvec, yvec, 'r.', label='Data', ms=8)
                    pl.plot(xplot, plt_line, label='Int')
                    ylim = pl.gca().get_ylim()
                    pl.plot([cent[ipk], cent[ipk]], ylim, 'r-.', label='Cent')
                    pl.plot([at_wave[-1], at_wave[-1]], ylim, 'g-', label='MAX')
                    pl.xlabel("Wavelength (A)")
                    pl.ylabel("Flux (DN)")
                    ptitle = "%3d/%3d: Wid, A, X, s = " \
                             "%2d, %8.1f, %9.3f, %9.3f" % \
                             ((ipk + 1), len(peaks), (x1 - x0) - 3, amps[ipk],
                              at_wave[-1], sigs[ipk])
                    pl.title(ptitle)
                    pl.legend()
                    pl.gca().margins(0)
                    q = input(ptitle + "; <cr> - Next, q to quit: ")
                    if 'Q' in q.upper():
                        do_inter = False
                        pl.ioff()
            else:
                self.log.info("rejected: %.2f, sig = %.2f" % (cent[ipk],
                                                              sigs[ipk]))
                rej_wave.append(cent[ipk])
                rej_amp.append(amps[ipk])
                nrej += 1
        self.log.info("Total lines = %d, N rejected = %d" % (len(at_wave),
                                                             nrej))

        if self.frame.inter() >= 2:
            pl.ion()
        else:
            pl.ioff()
        pl.clf()
        norm_fac = np.nanmax(atspec)
        pl.plot(subwvals, subyvals / np.nanmax(subyvals), label='RefArc')
        xlim = pl.gca().get_xlim()
        pl.plot(atwave, atspec / norm_fac, label='Atlas')
        pl.plot(xlim, [hgt, hgt] / norm_fac, '-.', color='grey', label='Height')
        pl.xlim(xlim)
        for iw, w in enumerate(at_wave):
            pl.plot([w, w], [at_amp[iw], at_amp[iw]] / norm_fac, 'kd')
        for iw, w in enumerate(rej_wave):
            pl.plot([w, w], [rej_amp[iw], rej_amp[iw]] / norm_fac, 'rd')
        pl.xlabel("Wavelength (A)")
        pl.ylabel("Flux (e-)")
        pl.title(self.frame.plotlabel() + " Ngood = %d, Nrej = %d" %
                 (len(at_wave), len(rej_wave)))
        pl.legend()
        if self.frame.inter() >= 2:
            input("Next? <cr>: ")
        else:
            pl.pause(self.frame.plotpause())
        # store results
        self.at_wave = at_wave

    def solve_arcs(self):
        """Solve the bar arc wavelengths"""
        if KcwiConf.INTER >= 2:
            master_inter = True
        else:
            master_inter = False
        if KcwiConf.INTER >= 3:
            do_inter = True
            pl.ion()
        else:
            do_inter = False
        # set thresh
        hgt = 50.
        self.log.info("line thresh = %.2f" % hgt)
        # store fit stats
        bar_sig = []
        bar_nls = []
        # loop over bars
        for ib, b in enumerate(self.arcs):
            print("")
            self.log.info("BAR %d" % ib)
            print("")
            coeff = self.twkcoeff[ib]
            # get pixel values
            xvals = np.arange(0, len(b))
            bw = np.polyval(coeff, xvals)
            # smooth spectrum according to slicer
            if 'Small' in self.frame.ifuname():
                bspec = b
            else:
                if 'Large' in self.frame.ifuname():
                    win = boxcar(5)
                else:
                    win = boxcar(3)
                bspec = sp.signal.convolve(b, win, mode='same') / sum(win)
            spmode = mode(np.round(bspec))
            spmed = np.nanmedian(bspec)
            self.log.info("Arc spec median = %.3f, mode = %d" %
                          (spmed, spmode.mode[0]))
            # store values to fit
            at_wave_dat = []
            ob_pix_dat = []
            nrej = 0
            # loop over lines
            for iw, aw in enumerate(self.at_wave):
                # get window for this line
                try:
                    line_x = [i for i, v in enumerate(bw) if v >= aw][0]
                    minow, maxow, count = get_line_window(bspec, line_x,
                                                          thresh=hgt)
                    if count < 5 or not minow or not maxow:
                        # self.log.info("Window error: count = %d, "
                        #              "skipping line at %.3f"
                        #              % (count, aw))
                        nrej += 1
                        continue
                    yvec = bspec[minow:maxow+1]
                    xvec = xvals[minow:maxow+1]
                    max_value = yvec[yvec.argmax()]
                    # Get interpolation
                    int_line = interpolate.interp1d(xvec, yvec, kind='cubic',
                                                    bounds_error=False,
                                                    fill_value='extrapolate')
                    xplot = np.linspace(min(xvec), max(xvec), num=1000)
                    # get peak value
                    plt_line = int_line(xplot)
                    max_index = plt_line.argmax()
                    peak = xplot[max_index]
                    # Calculate centroid
                    cent = np.sum(xvec * yvec) / np.sum(yvec)
                    if abs(cent - peak) > 0.7:
                        # print("High cent - peak offset: %.3f, skipping %.3f" %
                        #      (abs(cent - peak), aw))
                        nrej += 1
                        continue
                    # store data
                    ob_pix_dat.append(peak)
                    at_wave_dat.append(aw)
                    # plot, if requested
                    if do_inter:
                        pl.clf()
                        pl.plot(xvec, yvec, 'r.', label='Data')
                        pl.plot(xplot, plt_line, label='Int')
                        ylim = [0, pl.gca().get_ylim()[1]]
                        xlim = pl.gca().get_xlim()
                        pl.plot(xlim, [max_value*0.5, max_value*0.5], 'k--')
                        pl.plot([cent, cent], ylim, 'g--', label='Cntr')
                        pl.plot([line_x, line_x], ylim, 'r-.', label='X in')
                        pl.plot([peak, peak], ylim, 'c-.', label='Peak')
                        pl.xlabel("CCD Y (px)")
                        pl.ylabel("Flux (DN)")
                        pl.ylim(ylim)
                        ptitle = "Bar: %d - %3d/%3d: x0, x1, Cent, Wave = " \
                                 "%d, %d, %8.1f, %9.2f" % \
                                 (ib, (iw + 1), len(self.at_wave),
                                  minow, maxow, cent, aw)
                        pl.title(ptitle)
                        pl.legend()

                        q = input(ptitle + "; <cr> - Next, q to quit: ")
                        if 'Q' in q.upper():
                            do_inter = False
                            pl.ioff()
                except IndexError:
                    # self.log.info("Atlas line not in observation: %.2f" % aw)
                    nrej += 1
                    continue
                except ValueError:
                    self.log.info("Interpolation error for line at %.2f" % aw)
                    nrej += 1
            self.log.info("Fitting wavelength solution starting with %d "
                          "lines after rejecting %d lines" %
                          (len(ob_pix_dat), nrej))
            # Fit wavelengths
            # Initial fit
            wfit = np.polyfit(ob_pix_dat, at_wave_dat, 4)
            pwfit = np.poly1d(wfit)
            at_wave_fit = pwfit(ob_pix_dat)
            resid = at_wave_fit - at_wave_dat
            wsig = np.nanstd(resid)
            rej_wave = []
            rej_rsd = []
            # Iteratively remove outliers
            for it in range(3):
                self.log.info("Iteration %d" % it)
                ob_dat = []
                at_dat = []
                # Trim outliers
                for il, rsd in enumerate(resid):
                    if abs(rsd) < wsig * 2.5:
                        ob_dat.append(ob_pix_dat[il])
                        at_dat.append(at_wave_dat[il])
                    else:
                        self.log.info("REJ: %d, %.2f, %.3f" %
                                      (il, ob_pix_dat[il], at_wave_dat[il]))
                        rej_wave.append(at_wave_dat[il])
                        rej_rsd.append(rsd)
                # refit
                ob_pix_dat = ob_dat.copy()
                at_wave_dat = at_dat.copy()
                wfit = np.polyfit(ob_pix_dat, at_wave_dat, 4)
                pwfit = np.poly1d(wfit)
                at_wave_fit = pwfit(ob_pix_dat)
                resid = at_wave_fit - at_wave_dat
                wsig = np.nanstd(resid)
            # store results
            print("")
            self.log.info("Bar %03d, Slice = %02d, RMS = %.3f, N = %d" %
                          (ib, int(ib / 5), wsig, len(ob_pix_dat)))
            print("")
            self.fincoeff.append(wfit)
            bar_sig.append(wsig)
            bar_nls.append(len(ob_pix_dat))
            # plot bar fit residuals
            if master_inter:
                pl.ion()
                pl.clf()
                pl.plot(at_wave_dat, resid, 'd', label='Rsd')
                ylim = pl.gca().get_ylim()
                if rej_wave:
                    pl.plot(rej_wave, rej_rsd, 'rd', label='Rej')
                pl.xlabel("Wavelength (A)")
                pl.ylabel("Fit - Inp (A)")
                pl.title(self.frame.plotlabel() +
                         " Bar = %03d, Slice = %02d, RMS = %.3f, N = %d" %
                         (ib, int(ib / 5), wsig, len(ob_pix_dat)))
                xlim = [self.atminwave, self.atmaxwave]
                pl.plot(xlim, [0., 0.], '-')
                pl.plot(xlim, [wsig, wsig], '-.', color='gray')
                pl.plot(xlim, [-wsig, -wsig], '-.', color='gray')
                pl.plot([self.frame.cwave(), self.frame.cwave()], ylim, '-.',
                        label='CWAV')
                pl.xlim(xlim)
                pl.ylim(ylim)
                pl.legend()
                input("Next? <cr>: ")

                # overplot atlas and bar using fit wavelengths
                pl.clf()
                bwav = pwfit(xvals)
                pl.plot(bwav, b, label='Arc')
                ylim = pl.gca().get_ylim()
                atwave = self.refwave[self.atminrow:self.atmaxrow]
                atspec = self.reflux[self.atminrow:self.atmaxrow]
                atnorm = np.nanmax(b) / np.nanmax(atspec)
                pl.plot(atwave, atspec * atnorm, label='Atlas')
                pl.plot([self.frame.cwave(), self.frame.cwave()], ylim, '-.',
                        label='CWAV')
                pl.xlim(xlim)
                pl.ylim(ylim)
                pl.xlabel("Wavelength (A)")
                pl.ylabel("Flux")
                pl.title(self.frame.plotlabel() +
                         " Bar = %03d, Slice = %02d, RMS = %.3f, N = %d" %
                         (ib, int(ib/5), wsig, len(ob_pix_dat)))
                for w in at_wave_fit:
                    pl.plot([w, w], ylim, 'c-.')
                for w in rej_wave:
                    pl.plot([w, w], ylim, 'r-.')
                pl.legend()
                q = input("Next? <cr>, q - quit: ")
                if 'Q' in q.upper():
                    master_inter = False
                    pl.ioff()
        # Plot final results
        if self.frame.inter() >= 2:
            pl.ion()
        else:
            pl.ioff()
        # Plot fit sigmas
        pl.clf()
        pl.plot(bar_sig, 'd', label='RMS')
        xlim = [-1, 120]
        ylim = pl.gca().get_ylim()
        av_bar_sig = np.nanmean(bar_sig)
        st_bar_sig = np.nanstd(bar_sig)
        self.log.info("<STD>     = %.3f +- %.3f (A)" % (av_bar_sig, st_bar_sig))
        pl.plot(xlim, [av_bar_sig, av_bar_sig], 'k--')
        pl.plot(xlim, [(av_bar_sig-st_bar_sig), (av_bar_sig-st_bar_sig)], 'k:')
        pl.plot(xlim, [(av_bar_sig+st_bar_sig), (av_bar_sig+st_bar_sig)], 'k:')
        for ix in range(1, 24):
            sx = ix * 5 - 0.5
            pl.plot([sx, sx], ylim, '-.', color='black')
        pl.xlabel("Bar #")
        pl.ylabel("RMS (A)")
        pl.title(self.frame.plotlabel() + " <RMS>: %.3f +- %.3f" % (av_bar_sig,
                                                                    st_bar_sig))
        pl.xlim(xlim)
        pl.gca().margins(0)
        if self.frame.inter() >= 2:
            input("Next? <cr>: ")
        else:
            pl.pause(self.frame.plotpause())
        # Plot number of lines fit
        pl.clf()
        pl.plot(bar_nls, 'd', label='N lines')
        ylim = pl.gca().get_ylim()
        av_bar_nls = np.nanmean(bar_nls)
        st_bar_nls = np.nanstd(bar_nls)
        self.log.info("<N Lines> = %.1f +- %.1f" % (av_bar_nls, st_bar_nls))
        pl.plot(xlim, [av_bar_nls, av_bar_nls], 'k--')
        pl.plot(xlim, [(av_bar_nls-st_bar_nls), (av_bar_nls-st_bar_nls)], 'k:')
        pl.plot(xlim, [(av_bar_nls+st_bar_nls), (av_bar_nls+st_bar_nls)], 'k:')
        for ix in range(1, 24):
            sx = ix * 5 - 0.5
            pl.plot([sx, sx], ylim, '-.', color='black')
        pl.xlabel("Bar #")
        pl.ylabel("N Lines")
        pl.title(self.frame.plotlabel() + " <N Lines>: %.3f +- %.3f" %
                 (av_bar_nls, st_bar_nls))
        pl.xlim(xlim)
        pl.gca().margins(0)
        if self.frame.inter() >= 2:
            input("Next? <cr>: ")
        else:
            pl.pause(self.frame.plotpause())
        # Plot coefs
        ylabs = ['Ang/px^4', 'Ang/px^3', 'Ang/px^2', 'Ang/px', 'Ang']
        for ic in reversed(range(len(self.fincoeff[0]))):
            pl.clf()
            coef = []
            for c in self.fincoeff:
                coef.append(c[ic])
            pl.plot(coef, 'd')
            ylim = pl.gca().get_ylim()
            for ix in range(1, 24):
                sx = ix * 5 - 0.5
                pl.plot([sx, sx], ylim, '-.', color='black')
            pl.xlabel("Bar #")
            pl.ylabel("Coef %d (%s)" % (ic, ylabs[ic]))
            pl.title(self.frame.plotlabel() + " Coef %d" % ic)
            pl.xlim(xlim)
            pl.gca().margins(0)
            if self.frame.inter() >= 2:
                input("Next? <cr>: ")
            else:
                pl.pause(self.frame.plotpause())

    def solve_geom(self):
        self.log.info("solve_geom")

    def apply_flat(self):
        self.log.info("apply_flat")

    def subtract_sky(self):
        self.log.info("subtract_sky")

    def make_cube(self):
        self.log.info("make_cube")

    def apply_dar_correction(self):
        self.log.info("apply_dar_correction")

    def flux_calibrate(self):
        self.log.info("flux_calibrate")

    def make_invsensitivity(self):
        self.log.info("make_invsensitivity")
