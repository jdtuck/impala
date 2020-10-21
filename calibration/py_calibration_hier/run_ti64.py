##########################################
## stat model parameters

type = 'cluster' # cluster, hier, or pool
ntemps = 1 # number of temperatures if not using MPI
temperature_ladder_spacing = 1.1
nmcmc = 40
nburn = 20
nthin = 5

##########################################
## computation parameters

ncores = 2 # in addition to MPI processes
use_mpi = False

##########################################
## paths

data_path = './data/data_Ti64.db'

results_path = './results/Ti64/'
name = 'res_ti64_pool'

##########################################
## physics parameters

starting_consts = {
    'alpha'  : 0.2,
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
    'y1'    : (0.001,    0.1),
    'y2'    : (0.3,      1.),
    'vel'   : (0.99,     1.01),
    }
model_args = {'flow_stress_model'   : 'PTW', 'shear_modulus_model' : 'Stein'}


##########################################
##########################################
##########################################
import numpy as np
#from numpy import float64
#np.seterr(under = 'ignore')


if type == 'cluster':
    import sm_dpcluster as sm
elif type == 'hier':
    import sm_hier as sm
elif type == 'pool':
    import sm_pooled as sm

sm.POOL_SIZE = ncores

if use_mpi:
    from mpi4py import MPI
    import pt_mpi as pt
    pt.MPI_MESSAGE_SIZE = 2**15
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()
else:
    import pt

if __name__ == '__main__':


    if use_mpi:
        if rank > 0:
            chain = pt.PTSlave(comm=comm, statmodel=sm.Chain)
            chain.watch()

        elif rank == 0:
            model = pt.PTMaster(
                comm,
                temperature_ladder=temperature_ladder_spacing ** np.array(range(size - 1)),
                path=data_path,
                bounds=parameter_bounds,
                constants=starting_consts,
                model_args=model_args,
            )
            model.sample(nmcmc, nthin)
            model.write_to_disk(results_path + name + '.db', nburn, nthin)
            model.plot_accept_probability(results_path + name + '_accept.png', nburn)
            model.plot_swap_probability(results_path + name + '_swapped.png', nburn // nthin)
            model.complete()


    else:
        model = pt.PTMaster(
            statmodel=sm.Chain,
            temperature_ladder=temperature_ladder_spacing ** np.array(range(ntemps)),
            path=data_path,
            bounds=parameter_bounds,
            constants=starting_consts,
            model_args=model_args,
        )
        model.sample(nmcmc, nthin)
        model.write_to_disk(results_path + name + '.db', nburn, nthin)
        model.plot_accept_probability(results_path + name + '_accept.png', nburn)
        model.plot_swap_probability(results_path + name + '_swapped.png', nburn // nthin)
        model.complete()

# EOF