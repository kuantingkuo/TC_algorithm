import numpy as np
import numba
import xarray as xr
import yaml

@numba.njit
def haversine(lato, lono, lat, lon):
    distance = np.empty((len(lat), len(lon)))
    At = 6371.22
    Theta1 = np.deg2rad(lono)
    Phi1 = np.deg2rad(90. - lato)
    for j in range(len(lat)):
        for i in range(len(lon)):
            Theta2 = np.deg2rad(lon[i])
            Phi2 = np.deg2rad(90. - lat[j])
            temp = np.cos(Phi1) * np.cos(Phi2) + np.cos(Theta1 - Theta2) * np.sin(Phi1) * np.sin(Phi2)
            temp = np.minimum(temp, 1.)
            temp = np.maximum(temp, -1.)
            Alpha = np.arccos(temp)
            distance[j, i] = Alpha * At
    return distance

@numba.njit(parallel=True)
def vintp(var, pres, plevs):
    s = var.shape  # time, lev, lat, lon
    out = np.zeros((s[0], len(plevs), s[2], s[3]))
    for t in numba.prange(s[0]):
        for j in range(s[2]):
            for i in range(s[3]):
                out[t, :, j, i] = np.interp(plevs, pres[t, :, j, i], var[t, :, j, i])
#                out[t, :, j, i] = np.where(plevs > pres[t, -1, j, i], np.nan, out[t, :, j, i])
    return out

def pad_rolling_min2(arr):
    arrpad = arr.pad(lon=(2, 2), mode='wrap')
    arrroll = arrpad.rolling(lon=5, lat=5, center=True)
    arrmin = arrroll.min()[:, 3:-3, 2:-2]
    return arrmin

def pad_rolling_min(arr):
    arrpad = arr.pad(lon=(1, 1), mode='wrap')
    arrroll = arrpad.rolling(lon=3, lat=3, center=True)
    arrmin = arrroll.min()[:, 3:-3, 1:-1]
    return arrmin

def pad_rolling_max(arr):
    arrpad = arr.pad(lon=(1, 1), mode='wrap')
    arrroll = arrpad.rolling(lon=3, lat=3, center=True)
    arrmax = arrroll.max()[:, 3:-3, 1:-1]
    return arrmax

def pad_rolling_mean(arr):
    arrpad = arr.pad(lon=(3, 3), mode='wrap')
    arrroll = arrpad.rolling(lon=7, lat=7, center=True)
    arrmean = arrroll.mean()[:, 3:-3, 3:-3]
    return arrmean

def latpad(arr):
    arrpad = arr.pad(lat=(3, 3))
    shift = int(len(arr.lon) / 2)
    Spad = arr.isel(lat=[3, 2, 1]).roll(lon=shift)
    Npad = arr.isel(lat=[-2, -3, -4]).roll(lon=shift)
    arrpad.data[:, :3, :] = Spad
    arrpad.data[:, -3:, :] = Npad
    return arrpad

def uvlatpad(arr):
    arrpad = arr.pad(lat=(3, 3))
    shift = int(len(arr.lon) / 2)
    Spad = arr.isel(lat=[3, 2, 1]).roll(lon=shift)
    Npad = arr.isel(lat=[-2, -3, -4]).roll(lon=shift)
    arrpad.data[:, :3, :] = -Spad
    arrpad.data[:, -3:, :] = -Npad
    return arrpad

def mag(a, b):
    a.load()
    b.load()
    func = lambda x, y: np.sqrt(x ** 2 + y ** 2)
    return xr.apply_ufunc(func, a, b)

def first_nonzero(arr, axis, invalid_val=-999):  # numpy form
    mask = arr != 0
    return np.where(mask.any(axis=axis), mask.argmax(axis=axis), invalid_val)

def load_config(config_path="config.yaml"):
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)
