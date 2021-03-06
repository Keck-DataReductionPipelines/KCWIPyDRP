from KeckDRP import PrimitivesBASE
import numpy as np
import matplotlib.pyplot as pl
import math


class CcdPrimitives(PrimitivesBASE):

    def subtract_oscan(self):
        # image sections for each amp
        bsec, dsec, tsec, direc = self.map_ccd()
        namps = len(bsec)
        # polynomial fit order
        if namps == 4:
            porder = 2
        else:
            porder = 7
        # header keyword to update
        key = 'OSCANSUB'
        # is it performed?
        performed = False
        # loop over amps
        for ia in range(namps):
            # get gain
            gain = self.frame.header['GAIN%d' % (ia + 1)]
            # check if we have enough data to fit
            if (bsec[ia][3] - bsec[ia][2]) > self.frame.minoscanpix():
                # pull out an overscan vector
                x0 = bsec[ia][2] + self.frame.oscanbuf()
                x1 = bsec[ia][3] - self.frame.oscanbuf()
                y0 = bsec[ia][0]
                y1 = bsec[ia][1] + 1
                osvec = np.nanmedian(self.frame.data[y0:y1, x0:x1], axis=1)
                nsam = x1 - x0
                xx = np.arange(len(osvec), dtype=np.float)
                # fit it, avoiding first 50 px
                if direc[ia]:
                    # forward read skips first 50 px
                    oscoef = np.polyfit(xx[50:], osvec[50:], porder)
                else:
                    # reverse read skips last 50 px
                    oscoef = np.polyfit(xx[:-50], osvec[:-50], porder)
                # generate fitted overscan vector for full range
                osfit = np.polyval(oscoef, xx)
                # calculate residuals
                resid = (osvec - osfit) * math.sqrt(nsam) * gain / 1.414
                sdrs = float("%.3f" % np.std(resid))
                self.log.info("Amp%d Read noise from oscan in e-: %.3f" %
                              ((ia + 1), sdrs))
                self.frame.header['OSCNRN%d' % (ia + 1)] = \
                    (sdrs, "amp%d RN in e- from oscan" % (ia + 1))
                if self.frame.inter() >= 1:
                    # plot data and fit
                    pl.ion()
                    # fig = pl.figure(num=0, figsize=(17.0, 6.0))
                    # fig.canvas.set_window_title('KCWI DRP')
                    pl.plot(osvec)
                    legend = ["oscan", ]
                    pl.plot(osfit)
                    legend.append("fit")
                    pl.xlabel("pixel")
                    pl.ylabel("DN")
                    pl.legend(legend)
                    pl.title("Overscan img #%d amp #%d" % (
                        self.frame.header['FRAMENO'], (ia+1)))
                    if self.frame.inter() >= 2:
                        input("Next? <cr>: ")
                    else:
                        pl.pause(self.frame.plotpause())
                    pl.clf()
                # subtract it
                for ix in range(dsec[ia][2], dsec[ia][3]+1):
                    self.frame.data[y0:y1, ix] = \
                        self.frame.data[y0:y1, ix] - osfit
                performed = True
            else:
                self.log.info("not enough overscan px to fit amp %d")

        if performed:
            self.frame.header[key] = (True, self.keyword_comments[key])
        else:
            self.frame.header[key] = (False, self.keyword_comments[key])
        logstr = self.subtract_oscan.__module__ + "." + \
                 self.subtract_oscan.__qualname__
        self.frame.header['HISTORY'] = logstr
        self.log.info(self.subtract_oscan.__qualname__)

    def trim_oscan(self):
        # parameters
        # image sections for each amp
        bsec, dsec, tsec, direc = self.map_ccd()
        namps = len(dsec)
        # header keyword to update
        key = 'OSCANTRM'
        # get output image dimensions
        max_sec = max(tsec)
        # create new blank image
        new = np.zeros((max_sec[1]+1, max_sec[3]+1), dtype=np.float64)
        # loop over amps
        for ia in range(namps):
            # input range indices
            yi0 = dsec[ia][0]
            yi1 = dsec[ia][1] + 1
            xi0 = dsec[ia][2]
            xi1 = dsec[ia][3] + 1
            # output range indices
            yo0 = tsec[ia][0]
            yo1 = tsec[ia][1] + 1
            xo0 = tsec[ia][2]
            xo1 = tsec[ia][3] + 1
            # transfer to new image
            new[yo0:yo1, xo0:xo1] = self.frame.data[yi0:yi1, xi0:xi1]
            # update amp section
            sec = "[%d:" % (xo0+1)
            sec += "%d," % xo1
            sec += "%d:" % (yo0+1)
            sec += "%d]" % yo1
            self.frame.header['ATSEC%d' % (ia+1)] = sec
            # remove obsolete sections
            self.frame.header.pop('ASEC%d' % (ia + 1))
            self.frame.header.pop('BSEC%d' % (ia + 1))
            self.frame.header.pop('DSEC%d' % (ia + 1))
            self.frame.header.pop('CSEC%d' % (ia + 1))
        # update with new image
        self.frame.data = new
        self.frame.header['NAXIS1'] = max_sec[3] + 1
        self.frame.header['NAXIS2'] = max_sec[1] + 1
        self.frame.header[key] = (True, self.keyword_comments[key])

        logstr = self.trim_oscan.__module__ + "." + \
                 self.trim_oscan.__qualname__
        self.frame.header['HISTORY'] = logstr
        self.log.info(self.trim_oscan.__qualname__)

    def correct_gain(self):
        namps = self.frame.namps()
        for ia in range(namps):
            # get amp section
            sec, rfor = self.parse_imsec(
                section_key='ATSEC%d' % (ia + 1))
            # get gain for this amp
            gain = self.frame.header['GAIN%d' % (ia + 1)]
            self.log.info("Applying gain correction of %.3f in section %s" %
                          (gain, self.frame.header['ATSEC%d' % (ia + 1)]))
            self.frame.data[sec[0]:(sec[1]+1), sec[2]:(sec[3]+1)] *= gain

        self.frame.header['GAINCOR'] = (True, self.keyword_comments['GAINCOR'])
        self.frame.header['BUNIT'] = ('electron',
                                      self.keyword_comments['BUNIT'])
        self.frame.unit = 'electron'

        logstr = self.correct_gain.__module__ + "." + \
                 self.correct_gain.__qualname__
        self.frame.header['HISTORY'] = logstr
        self.log.info(self.correct_gain.__qualname__)

    def remove_crs(self):
        self.log.info("remove_crs")

    def remove_badcols(self):
        self.log.info("remove_badcols")

    def rectify_image(self):
        """Rotate images based on ampmode"""
        ampmode = self.frame.header['AMPMODE'].strip().upper()
        if '__B' in ampmode or '__G' in ampmode:
            newimg = np.rot90(self.frame.data, 2)
            newunc = np.rot90(self.frame.uncertainty.array, 2)
            self.frame.data = newimg
            self.frame.uncertainty.array = newunc
        elif '__D' in ampmode or '__F' in ampmode:
            newimg = np.fliplr(self.frame.data)
            newunc = np.fliplr(self.frame.uncertainty.array)
            self.frame.data = newimg
            self.frame.uncertainty.array = newunc
        elif '__A' in ampmode or '__H' in ampmode or 'TUP' in ampmode:
            newimg = np.flipud(self.frame.data)
            newunc = np.flipud(self.frame.uncertainty.array)
            self.frame.data = newimg
            self.frame.uncertainty.array = newunc
        logstr = self.rectify_image.__module__ + "." + \
                 self.rectify_image.__qualname__
        self.frame.header['HISTORY'] = logstr
        self.log.info(self.rectify_image.__qualname__)

    def parse_imsec(self, section_key=None):
        if section_key is None:
            return None, None
        else:
            # forward read?
            xfor = True
            yfor = True
            section = self.frame.header[section_key]
            p1 = int(section[1:-1].split(',')[0].split(':')[0])
            p2 = int(section[1:-1].split(',')[0].split(':')[1])
            p3 = int(section[1:-1].split(',')[1].split(':')[0])
            p4 = int(section[1:-1].split(',')[1].split(':')[1])
            # tests for individual axes
            if p1 > p2:
                x0 = p2 - 1
                x1 = p1 - 1
                xfor = False
            else:
                x0 = p1 - 1
                x1 = p2 - 1
            if p3 > p4:
                y0 = p4 - 1
                y1 = p3 - 1
                yfor = False
            else:
                y0 = p3 - 1
                y1 = p4 - 1
            # package output
            sec = (y0, y1, x0, x1)
            rfor = (yfor, xfor)
            # use python axis ordering
            return sec, rfor

    def map_ccd(self):
        """Return CCD section variables useful for processing

        Uses FITS keyword NVIDINP to determine how many amplifiers were used
        to read out the CCD.  Then reads the corresponding BSECn, and
        DSECn keywords, where n is the amplifier number.  The indices are
        converted to Python (0-biased, y axis first) indices and an array
        is constructed for each of the two useful sections of the CCD as
        follows:

        Bsec[0][0] - First amp, y lower limit
        Bsec[0][1] - First amp, y upper limit
        Bsec[0][2] - First amp, x lower limit
        Bsec[0][3] - First amp, x upper limit
        Bsec[1][0] - Second amp, y lower limit
        etc.

        Bsec is the full overscan region for the given amplifier and is used
        to calculate and perform the overscan subtraction.

        Dsec is the full CCD region for the given amplifier and is used to
        trim the image after overscan subtraction has been performed.

        Tsec accounts for trimming the image according to Dsec.

        Amps are assumed to be organized as follows:

        (0,ny)	--------- (nx,ny)
                | 3 | 4 |
                ---------
                | 1 | 2 |
        (0,0)	--------- (nx, 0)

        Args:
        -----
            self: instance of CcdPrimitive class (automatic)

        Returns:
        --------
            list: (int) y0, y1, x0, x1 for bias section
            list: (int) y0, y1, x0, x1 for data section
            list: (int) y0, y1, x0, x1 for trimmed section
            list: (bool) y-direction, x-direction, True if forward, else False
        """

        namps = self.frame.namps()
        # TODO: check namps
        # section lists
        bsec = []
        dsec = []
        tsec = []
        direc = []
        # loop over amps
        for i in range(namps):
            sec, rfor = self.parse_imsec(section_key='BSEC%d' % (i+1))
            bsec.append(sec)
            sec, rfor = self.parse_imsec(section_key='DSEC%d' % (i+1))
            dsec.append(sec)
            direc.append(rfor)
            if i == 0:
                y0 = 0
                y1 = sec[1] - sec[0]
                x0 = 0
                x1 = sec[3] - sec[2]
            elif i == 1:
                y0 = 0
                y1 = sec[1] - sec[0]
                x0 = tsec[0][3] + 1
                x1 = x0 + sec[3] - sec[2]
            elif i == 2:
                y0 = tsec[0][1] + 1
                y1 = y0 + sec[1] - sec[0]
                x0 = 0
                x1 = sec[3] - sec[2]
            elif i == 3:
                y0 = tsec[0][1] + 1
                y1 = y0 + sec[1] - sec[0]
                x0 = tsec[0][3] + 1
                x1 = x0 + sec[3] - sec[2]
            else:
                # should not get here
                y0 = -1
                y1 = -1
                x0 = -1
                x1 = -1
                self.log.info("ERROR - bad amp number: %d" % i)
            tsec.append((y0, y1, x0, x1))

        return bsec, dsec, tsec, direc

