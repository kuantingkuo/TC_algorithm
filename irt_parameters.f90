MODULE irt_parameters
! grid information
INTEGER, PARAMETER    :: domainsize_x = 288
INTEGER, PARAMETER    :: domainsize_y = 192

LOGICAL, PARAMETER    :: llonlatgrid = .TRUE.
REAL, PARAMETER       :: unit_area = 10000. ! in 1degx1deg -> about 10000.km2
! only used if llonlatgrid=.TRUE., otherwise set to arbitrary value:
REAL, PARAMETER       :: lat_first = -90.0000000
REAL, PARAMETER       :: lat_inc = 0.942408377
REAL, PARAMETER       :: lon_inc = 1.2500000

LOGICAL, PARAMETER    :: lperiodic_x = .TRUE.
LOGICAL, PARAMETER    :: lperiodic_y = .FALSE.
LOGICAL, PARAMETER    :: lpole = .FALSE. ! .FALSE.

INTEGER, PARAMETER    :: n_fields = 0   ! number of additional averaging

! bins of coarse velocity field
INTEGER, PARAMETER    :: time_steps = 361
INTEGER, PARAMETER    :: nt_bins = 24         ! 24 hours
INTEGER, PARAMETER    :: nx_bins = 1
INTEGER, PARAMETER    :: ny_bins = 1

REAL, PARAMETER       :: threshold = 0.5          ! for intensity
REAL, PARAMETER       :: minimum_size = 1.       ! events smaller than that will be sorted out

REAL, PARAMETER       :: termination_sensitivity=1.     ! Choose value between 0.0 and 1.0

REAL, PARAMETER       :: max_velocity = 10.   ! adjust acordingly
                                              ! velocities>max_velocity will be ignored to remove outliers
! define a minimal number of cells required for a coarse grained coordinate to be evaluated 
! if there are less, missing value will be assigned to that coarse cell
INTEGER, PARAMETER    :: min_cells = 1

INTEGER, PARAMETER    :: max_no_of_cells=500000  ! buffer size, increase if necessary
INTEGER, PARAMETER    :: max_no_of_tracks=100000    ! buffer size, increase if necessary
INTEGER, PARAMETER    :: max_length_of_track=3000  ! buffer size, increase if necessary


REAL, PARAMETER       :: miss=-9.99e8           ! value<miss ==> missing_value

END MODULE irt_parameters
