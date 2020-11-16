import numpy as np
import time
from numpy import array, float64
np.seterr(under = 'ignore')
from mpi4py import MPI

# import sm_dpcluster as sm
import sm_pooled as sm
# import sm_hier as sm
import pt
# import pt_mpi as pt
# pt.MPI_MESSAGE_SIZE = 2**12
sm.POOL_SIZE = 8


# comm = MPI.COMM_WORLD
# rank = comm.Get_rank()
# size = comm.Get_size()

rank = 0
size = 3

material = 'copper'

# Defining Paths, Constants, Parameter Ranges
if True:
    if material == 'Al5083':
        path = './data/data_Al5083.db'
        starting_consts = {
            'y1'     : 0.094, 'y2'      : 0.575, 'beta' : 0.25,
            'alpha'  : 0.2,   'matomic' : 27.,   'Tref' : 298.,
            'Tmelt0' : 933.,  'rho0'    : 2.683, 'Cv0'  : 0.9e-5,
            'G0'     : 0.70,  'chi'     : 0.90,
            }
        parameter_bounds = {
            'theta0' : (0.0001,   0.05),
            'p'     : (0.0001,   5.),
            's0'    : (0.0001,   0.05),
            'sInf'  : (0.0001,   0.005),
            'kappa' : (0.0001,   0.5),
            'gamma' : (0.000001, 0.0001),
            'y0'    : (0.0001,   0.005),
            'yInf'  : (0.0001,   0.005),
            }
    if material == 'copper':
        path = './data/data_copper.db'
        parameter_bounds = {
            'theta0' : (0.0001,   0.1),
            'p'     : (0.0001,   10.),
            's0'    : (0.0001,   0.05),
            'sInf'  : (0.0001,   0.05),
            'kappa' : (0.0001,   1.),
            'gamma' : (0.000001, 0.1),
            'y0'    : (0.0001,   0.05),
            'yInf'  : (0.0001,   0.04),
            'y1'    : (0.001, 0.11),
            'y2'    : (-5.8, 1.),
            'beta'  : (0.09, 0.36),
            }
        starting_consts = {
            'alpha'  : 0.2,    'matomic' : 63.546, 'Tref' : 298.,
            'Tmelt0' : 1358.,  'rho0'    : 8.96,   'Cv0'  : 0.385e-5,
            'G0'     : 0.70,   'chi'     : 0.95,
            }
    if material == 'Ti64':
        path = './data/data_Ti64.db'
        starting_consts = {
            'alpha'  : 0.2,
            'y1'     : 0.0245,
            'y2'     : 0.33,
            'beta'   : 0.33,
            'matomic': 45.9,
            'Tmelt0' : 2110.,
            'rho0'   : 4.419,
            'Cv0'    : 0.525e-5,
            'G0'     : 0.4,
            'chi'    : 1.0,
            'sgB'    : 6.44e-4
            }
        parameter_bounds = {
            'theta0' : (0.0001,   0.2),
            'p'     : (0.0001,   5.),
            's0'    : (0.0001,   0.05),
            'sInf'  : (0.0001,   0.05),
            'kappa' : (0.0001,   0.5),
            'gamma' : (0.000001, 0.0001),
            'y0'    : (0.0001,   0.05),
            'yInf'  : (0.0001,   0.01),
            }

if __name__ == '__main__':
    if rank > 0:
        pass
        # chain = pt.PTSlave(comm = comm, statmodel = sm.Chain)
        # chain.watch()

    elif rank == 0:
        model = pt.PTMaster(
            # comm,
            statmodel = sm.Chain,
            temperature_ladder = 1.1 ** array(range(size - 1)),
            path       = path,
            bounds     = parameter_bounds,
            constants  = starting_consts,
            # model_args = {'flow_stress_model'   : 'PTW', 'shear_modulus_model' : 'Stein'},
            model_args = {'flow_stress_model'   : 'PTW', 'shear_modulus_model' : 'Simple'},
            )
        # model.sample(20000, 5)
        # model.write_to_disk('./results/Al5083/results_hier_Al5083.db', 10000, 5)
        # model.plot_swap_probability('./results/Al5083/results_hier_Al5083_swapped.png', 10000)
        # model.plot_accept_probability('./results/Al5083/results_hier_Al5083_accept.png', 10000)
        # model.complete()
        pass

# EOF