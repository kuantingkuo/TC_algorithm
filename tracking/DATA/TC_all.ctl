DSET ^tc1km20170601.TC.nc
DTYPE netcdf
TITLE atm output
options 365_day_calendar
UNDEF 1.e+36
XDEF 288 LINEAR 0 1.25
YDEF 192 LINEAR -90 0.94240837696335078534031413612565
ZDEF 1 levels 992.556095123291
TDEF 2208 LINEAR 00:00Z01Mar0001 1hr
VARS 8
vort=>vort 1 t,y,x 7x7 max vorticity [1/s]
V850=>V850 1 t,y,x 7x7 max velocity [m/s]
V300=>V300 1 t,y,x 7x7 max velocity [m/s]
warmcore=>warmcore 1 t,y,x 7x7 temperature anomaly
TC=>TC 1 t,y,x TC mask
SLP=>SLP 1 t,y,x SLP - 7x7 mean SLP
PS=>PS 1 t,y,x PS - 7x7 mean PS
vortmin=>vortmin 1 t,y,x 3x3 minimum vorticity
ENDVARS
