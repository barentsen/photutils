# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import math
import warnings

import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u
from astropy.utils.exceptions import AstropyUserWarning
from astropy.wcs.utils import skycoord_to_pixel

from .core import (SkyAperture, PixelAperture, _sanitize_pixel_positions,
                   _make_annulus_path, _get_phot_extents, _calc_aperture_var)
from ..utils.wcs_helpers import (skycoord_to_pixel_scale_angle, assert_angle,
                                 assert_angle_or_pixel)


skycoord_to_pixel_mode = 'all'


__all__ = ['RectangularAperture', 'RectangularAnnulus',
           'SkyRectangularAperture', 'SkyRectangularAnnulus']


def do_rectangular_photometry(data, positions, w, h, theta, error,
                              pixelwise_error, method, subpixels,
                              reduce='sum', w_in=None):

    extents = np.zeros((len(positions), 4), dtype=int)

    # TODO: this is an overestimate by up to sqrt(2) unless theta = 45 deg
    radius = max(h, w) * (2 ** -0.5)

    extents[:, 0] = positions[:, 0] - radius + 0.5
    extents[:, 1] = positions[:, 0] + radius + 1.5
    extents[:, 2] = positions[:, 1] - radius + 0.5
    extents[:, 3] = positions[:, 1] + radius + 1.5

    ood_filter, extent, phot_extent = _get_phot_extents(data, positions,
                                                        extents)

    flux = u.Quantity(np.zeros(len(positions), dtype=np.float), unit=data.unit)

    if error is not None:
        fluxvar = u.Quantity(np.zeros(len(positions), dtype=np.float),
                             unit=error.unit ** 2)

    # TODO: flag these objects
    if np.sum(ood_filter):
        flux[ood_filter] = np.nan
        warnings.warn("The aperture at position {0} does not have any "
                      "overlap with the data"
                      .format(positions[ood_filter]),
                      AstropyUserWarning)
        if np.sum(ood_filter) == len(positions):
            return flux

    x_min, x_max, y_min, y_max = extent
    x_pmin, x_pmax, y_pmin, y_pmax = phot_extent

    if method in ('center', 'subpixel'):
        if method == 'center':
            method = 'subpixel'
            subpixels = 1

        from ..geometry import rectangular_overlap_grid

        for i in range(len(flux)):
            if not np.isnan(flux[i]):

                fraction = rectangular_overlap_grid(x_pmin[i], x_pmax[i],
                                                    y_pmin[i], y_pmax[i],
                                                    x_max[i] - x_min[i],
                                                    y_max[i] - y_min[i],
                                                    w, h, theta, 0, subpixels)
                if w_in is not None:
                    h_in = w_in * h / w
                    fraction -= rectangular_overlap_grid(x_pmin[i], x_pmax[i],
                                                         y_pmin[i], y_pmax[i],
                                                         x_max[i] - x_min[i],
                                                         y_max[i] - y_min[i],
                                                         w_in, h_in, theta,
                                                         0, subpixels)

                flux[i] = np.sum(data[y_min[i]:y_max[i],
                                      x_min[i]:x_max[i]] * fraction)
                if error is not None:
                    fluxvar[i] = _calc_aperture_var(
                        data, fraction, error, flux[i], x_min[i], x_max[i],
                        y_min[i], y_max[i], pixelwise_error)

    if error is None:
        return flux
    else:
        return flux, np.sqrt(fluxvar)


def get_rectangular_fractions(data, positions, w, h, theta, method,
                              subpixels, reduce='sum', w_in=None):

    extents = np.zeros((len(positions), 4), dtype=int)

    # TODO: this is an overestimate by up to sqrt(2) unless theta = 45 deg
    radius = max(h, w) * (2 ** -0.5)

    extents[:, 0] = positions[:, 0] - radius + 0.5
    extents[:, 1] = positions[:, 0] + radius + 1.5
    extents[:, 2] = positions[:, 1] - radius + 0.5
    extents[:, 3] = positions[:, 1] + radius + 1.5

    ood_filter, extent, phot_extent = _get_phot_extents(data, positions,
                                                        extents)

    fractions = np.zeros((data.shape[0], data.shape[1], len(positions)),
                         dtype=np.float)

    if np.sum(ood_filter):
        warnings.warn("The aperture at position {0} does not have any "
                      "overlap with the data"
                      .format(positions[ood_filter]),
                      AstropyUserWarning)
        if np.sum(ood_filter) == len(positions):
            return np.squeeze(fractions)

    x_min, x_max, y_min, y_max = extent
    x_pmin, x_pmax, y_pmin, y_pmax = phot_extent

    if method in ('center', 'subpixel'):
        if method == 'center':
            method = 'subpixel'
            subpixels = 1

        from ..geometry import rectangular_overlap_grid

        for i in range(len(positions)):

            if ood_filter[i] is not True:

                fractions[y_min[i]: y_max[i], x_min[i]: x_max[i], i] = \
                    rectangular_overlap_grid(x_pmin[i], x_pmax[i],
                                             y_pmin[i], y_pmax[i],
                                             x_max[i] - x_min[i],
                                             y_max[i] - y_min[i],
                                             w, h, theta, 0, subpixels)
                if w_in is not None:
                    h_in = w_in * h / w
                    fractions[y_min[i]: y_max[i], x_min[i]: x_max[i], i] -= \
                        rectangular_overlap_grid(x_pmin[i], x_pmax[i],
                                                 y_pmin[i], y_pmax[i],
                                                 x_max[i] - x_min[i],
                                                 y_max[i] - y_min[i],
                                                 w_in, h_in, theta,
                                                 0, subpixels)

    return np.squeeze(fractions)


class SkyRectangularAperture(SkyAperture):
    """
    Rectangular aperture(s), defined in sky coordinates.

    Parameters
    ----------
    positions : `~astropy.coordinates.SkyCoord`
        Celestial coordinates of the aperture center(s). This can be either
        scalar coordinates or an array of coordinates.
    w : `~astropy.units.Quantity`
        The full width of the aperture(s) (at theta = 0, this is the "x"
        axis), either in angular or pixel units.
    h :  `~astropy.units.Quantity`
        The full height of the aperture(s) (at theta = 0, this is the "y"
        axis), either in angular or pixel units.
    theta : `~astropy.units.Quantity`
        The position angle of the width side in radians
        (counterclockwise).
    """

    def __init__(self, positions, w, h, theta):

        if isinstance(positions, SkyCoord):
            self.positions = positions
        else:
            raise TypeError("positions should be a SkyCoord instance")

        assert_angle_or_pixel('w', w)
        assert_angle_or_pixel('h', h)
        assert_angle('theta', theta)

        if w.unit.physical_type != h.unit.physical_type:
            raise ValueError("'w' and 'h' should either both be angles or "
                             "in pixels")

        self.w = w
        self.h = h
        self.theta = theta

    def to_pixel(self, wcs):
        """
        Convert the aperture to a `RectangularAperture` instance in
        pixel coordinates.
        """

        x, y = skycoord_to_pixel(self.positions, wcs,
                                 mode=skycoord_to_pixel_mode)
        central_pos = SkyCoord([wcs.wcs.crval], frame=self.positions.name,
                               unit=wcs.wcs.cunit)
        xc, yc, scale, angle = skycoord_to_pixel_scale_angle(central_pos, wcs)

        if self.w.unit.physical_type == 'angle':
            w = (scale * self.w).to(u.pixel).value
            h = (scale * self.h).to(u.pixel).value
        else:
            # pixels
            w = self.w.value
            h = self.h.value

        theta = (angle + self.theta).to(u.radian).value

        pixel_positions = np.array([x, y]).transpose()

        return RectangularAperture(pixel_positions, w, h, theta)


class RectangularAperture(PixelAperture):
    """
    Rectangular aperture(s), defined in pixel coordinates.

    Parameters
    ----------
    positions : tuple, or list, or array
        Pixel coordinates of the aperture center(s), either as a single
        ``(x, y)`` tuple, a list of ``(x, y)`` tuples, an ``Nx2`` or
        ``2xN`` `~numpy.ndarray`, or an ``Nx2`` or ``2xN``
        `~astropy.units.Quantity` in units of pixels.  A ``2x2``
        `~numpy.ndarray` or `~astropy.units.Quantity` is interpreted as
        ``Nx2``, i.e. two rows of (x, y) coordinates.
    w : float
        The full width of the aperture (at theta = 0, this is the "x" axis).
    h : float
        The full height of the aperture (at theta = 0, this is the "y" axis).
    theta : float
        The position angle of the width side in radians
        (counterclockwise).

    Raises
    ------
    ValueError : `ValueError`
        If either width (``w``) or height (``h``) is negative.
    """

    def __init__(self, positions, w, h, theta):
        try:
            self.w = float(w)
            self.h = float(h)
            self.theta = float(theta)
        except TypeError:
            raise TypeError("'w' and 'h' and 'theta' must "
                            "be numeric, received {0} and {1} and {2}."
                            .format((type(w), type(h), type(theta))))
        if w < 0 or h < 0:
            raise ValueError("'w' and 'h' must be nonnegative.")

        self.positions = _sanitize_pixel_positions(positions)

    def area(self):
        return self.w * self.h

    def plot(self, origin=(0, 0), source_id=None, ax=None, fill=False,
             **kwargs):

        import matplotlib.patches as mpatches

        plot_positions, ax, kwargs = self._prepare_plot(
            origin, source_id, ax, fill, **kwargs)

        hw = self.w / 2.
        hh = self.h / 2.
        sint = math.sin(self.theta)
        cost = math.cos(self.theta)
        dx = (hh * sint) - (hw * cost)
        dy = -(hh * cost) - (hw * sint)
        plot_positions = plot_positions + np.array([dx, dy])
        theta_deg = self.theta * 180. / np.pi
        for position in plot_positions:
            patch = mpatches.Rectangle(position, self.w, self.h, theta_deg,
                                       **kwargs)
            ax.add_patch(patch)

    def do_photometry(self, data, error=None, pixelwise_error=True,
                      method='subpixel', subpixels=5):

        if method == 'exact':
            warnings.warn("'exact' method is not implemented, defaults to "
                          "'subpixel' method and subpixels=32 instead",
                          AstropyUserWarning)
            method = 'subpixel'
            subpixels = 32

        elif method not in ('center', 'subpixel'):
            raise ValueError('{0} method not supported for aperture class '
                             '{1}'.format(method, self.__class__.__name__))

        flux = do_rectangular_photometry(data, self.positions,
                                         self.w, self.h, self.theta,
                                         error=error,
                                         pixelwise_error=pixelwise_error,
                                         method=method,
                                         subpixels=subpixels)
        return flux

    def get_fractions(self, data, method='exact', subpixels=5):

        if method == 'exact':
            warnings.warn("'exact' method is not implemented, defaults to "
                          "'subpixel' method and subpixels=32 instead",
                          AstropyUserWarning)
            method = 'subpixel'
            subpixels = 32

        elif method not in ('center', 'subpixel'):
            raise ValueError('{0} method not supported for aperture class '
                             '{1}'.format(method, self.__class__.__name__))

        fractions = get_rectangular_fractions(data, self.positions,
                                              self.w, self.h, self.theta,
                                              method=method,
                                              subpixels=subpixels)

        return fractions


class SkyRectangularAnnulus(SkyAperture):
    """
    Rectangular annulus aperture(s), defined in sky coordinates.

    Parameters
    ----------
    positions : `~astropy.coordinates.SkyCoord`
        Celestial coordinates of the aperture center(s). This can be either
        scalar coordinates or an array of coordinates.
    w_in : `~astropy.units.Quantity`
        The inner full width of the aperture(s), either in angular or pixel
        units.
    w_out : `~astropy.units.Quantity`
        The outer full width of the aperture(s), either in angular or pixel
        units.
    h_out : `~astropy.units.Quantity`
        The outer full height of the aperture(s), either in angular or pixel
        units. (The inner full height is determined by scaling by
        w_in/w_out.)
    theta : `~astropy.units.Quantity`
        The position angle of the semimajor axis (counterclockwise), either
        in angular or pixel units.
    """

    def __init__(self, positions, w_in, w_out, h_out, theta):

        if isinstance(positions, SkyCoord):
            self.positions = positions
        else:
            raise TypeError("positions should be a SkyCoord instance")
        assert_angle_or_pixel('w_in', w_in)
        assert_angle_or_pixel('w_out', w_out)
        assert_angle_or_pixel('h_out', h_out)
        assert_angle('theta', theta)

        if w_in.unit.physical_type != w_out.unit.physical_type:
            raise ValueError("w_in and w_out should either both be angles or "
                             "in pixels")

        if w_out.unit.physical_type != h_out.unit.physical_type:
            raise ValueError("w_out and h_out should either both be angles "
                             "or in pixels")

        self.w_in = w_in
        self.w_out = w_out
        self.h_out = h_out
        self.theta = theta

    def to_pixel(self, wcs):
        """
        Convert the aperture to a `EllipticalAnnulus` instance in pixel
        coordinates.
        """

        x, y = skycoord_to_pixel(self.positions, wcs,
                                 mode=skycoord_to_pixel_mode)
        central_pos = SkyCoord([wcs.wcs.crval], frame=self.positions.name,
                               unit=wcs.wcs.cunit)
        xc, yc, scale, angle = skycoord_to_pixel_scale_angle(central_pos, wcs)

        if self.w_in.unit.physical_type == 'angle':
            w_in = (scale * self.w_in).to(u.pixel).value
            w_out = (scale * self.w_out).to(u.pixel).value
            h_out = (scale * self.h_out).to(u.pixel).value
        else:
            # pixels
            w_in = self.w_in.value
            w_out = self.w_out.value
            h_out = self.h_out.value

        theta = (angle + self.theta).to(u.radian).value

        pixel_positions = np.array([x, y]).transpose()

        return RectangularAnnulus(pixel_positions, w_in, w_out, h_out, theta)


class RectangularAnnulus(PixelAperture):
    """
    Rectangular annulus aperture(s), defined in pixel coordinates.

    Parameters
    ----------
    positions : tuple, list, array, or `~astropy.units.Quantity`
        Pixel coordinates of the aperture center(s), either as a single
        ``(x, y)`` tuple, a list of ``(x, y)`` tuples, an ``Nx2`` or
        ``2xN`` `~numpy.ndarray`, or an ``Nx2`` or ``2xN``
        `~astropy.units.Quantity` in units of pixels.  A ``2x2``
        `~numpy.ndarray` or `~astropy.units.Quantity` is interpreted as
        ``Nx2``, i.e. two rows of (x, y) coordinates.
    w_in : float
        The inner full width of the aperture.
    w_out : float
        The outer full width of the aperture.
    h_out : float
        The outer full height of the aperture. (The inner full height is
        determined by scaling by w_in/w_out.)
    theta : float
        The position angle of the width side in radians.
        (counterclockwise).

    Raises
    ------
    ValueError : `ValueError`
        If inner width (``w_in``) is greater than outer width (``w_out``).
    ValueError : `ValueError`
        If either the inner width (``w_in``) or the outer height (``h_out``)
        is negative.
    """

    def __init__(self, positions, w_in, w_out, h_out, theta):
        try:
            self.w_in = float(w_in)
            self.w_out = float(w_out)
            self.h_out = float(h_out)
            self.theta = float(theta)
        except TypeError:
            raise TypeError("'w_in' and 'w_out' and 'h_out' and 'theta' must "
                            "be numeric, received {0} and {1} and {2} and "
                            "{3}.".format((type(w_in), type(w_out),
                                           type(h_out), type(theta))))

        if not (w_out > w_in):
            raise ValueError("'w_out' must be greater than 'w_in'")
        if w_in < 0 or h_out < 0:
            raise ValueError("'w_in' and 'h_out' must be non-negative")

        self.h_in = w_in * h_out / w_out

        self.positions = _sanitize_pixel_positions(positions)

    def area(self):
        return self.w_out * self.h_out - self.w_in * self.h_in

    def plot(self, origin=(0, 0), source_id=None, ax=None, fill=False,
             **kwargs):

        import matplotlib.patches as mpatches

        plot_positions, ax, kwargs = self._prepare_plot(
            origin, source_id, ax, fill, **kwargs)

        sint = math.sin(self.theta)
        cost = math.cos(self.theta)
        theta_deg = self.theta * 180. / np.pi

        hw_inner = self.w_in / 2.
        hh_inner = self.h_in / 2.
        dx_inner = (hh_inner * sint) - (hw_inner * cost)
        dy_inner = -(hh_inner * cost) - (hw_inner * sint)
        positions_inner = plot_positions + np.array([dx_inner, dy_inner])
        hw_outer = self.w_out / 2.
        hh_outer = self.h_out / 2.
        dx_outer = (hh_outer * sint) - (hw_outer * cost)
        dy_outer = -(hh_outer * cost) - (hw_outer * sint)
        positions_outer = plot_positions + np.array([dx_outer, dy_outer])

        for i, position_inner in enumerate(positions_inner):
            patch_inner = mpatches.Rectangle(position_inner,
                                             self.w_in, self.h_in,
                                             theta_deg, **kwargs)
            patch_outer = mpatches.Rectangle(positions_outer[i],
                                             self.w_out, self.h_out,
                                             theta_deg, **kwargs)
            path = _make_annulus_path(patch_inner, patch_outer)
            patch = mpatches.PathPatch(path, **kwargs)
            ax.add_patch(patch)

    def do_photometry(self, data, error=None, pixelwise_error=True,
                      method='subpixel', subpixels=5):

        if method == 'exact':
            warnings.warn("'exact' method is not implemented, defaults to "
                          "'subpixel' instead", AstropyUserWarning)
            method = 'subpixel'

        elif method not in ('center', 'subpixel'):
            raise ValueError('{0} method not supported for aperture class '
                             '{1}'.format(method, self.__class__.__name__))

        flux = do_rectangular_photometry(data, self.positions,
                                         self.w_out, self.h_out, self.theta,
                                         error=error,
                                         pixelwise_error=pixelwise_error,
                                         method=method, subpixels=subpixels,
                                         w_in=self.w_in)

        return flux

    def get_fractions(self, data, method='exact', subpixels=5):

        if method == 'exact':
            warnings.warn("'exact' method is not implemented, defaults to "
                          "'subpixel' method and subpixels=32 instead",
                          AstropyUserWarning)
            method = 'subpixel'
            subpixels = 32

        elif method not in ('center', 'subpixel'):
            raise ValueError('{0} method not supported for aperture class '
                             '{1}'.format(method, self.__class__.__name__))

        fractions = get_rectangular_fractions(data, self.positions,
                                              self.w_out, self.h_out,
                                              self.theta,
                                              method=method,
                                              subpixels=subpixels,
                                              w_in=self.w_in)

        return fractions
