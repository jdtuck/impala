"""
statistical_models_hier_redux.py

Defines the Statistical models

Chain      - Defines the Chain object (Hierarchy)
SubChainHB - Defines the Subchain relating to Hopkinson Bar (1 Experiment)
"""
import numpy as np
import pandas as pd

# from mpi4py import MPI
from scipy.stats import invgamma, invwishart, norm, multivariate_normal as mvnorm
from scipy.special import erf, erfinv
from numpy.linalg import cholesky
from numpy.random import normal, uniform
from random import sample, shuffle
from math import ceil, sqrt, pi, log, exp
from itertools import combinations
from pointcloud import localcov

from submodel import SubModelHB, SubModelTC, SubModelFP
from transport import TransportHB, TransportTC, TransportFP
from physical_models_c import MaterialModelltivariate_normal as mvnorm

import matplotlib.pyplot as plt
import seaborn as sea
import time

sea.set(style = 'ticks')

# class MPI_Error(Exception):
#     pass

class BreakException(Exception):
    pass

# Statistical Models

class Transformer(object):
    """ Class to hold transformations (logit, probit) """
    parameter_order = []

    @staticmethod
    def logit(x):
        """
        Logit Transformation:
        For x bounded to 0-1, y is unbounded; between -inf and inf
        """
        return np.log(x / (1 - x))

    @staticmethod
    def invlogit(y):
        """
        Inverse Logit Transformation:
        For real-valued variable y, result x is bounded 0-1
        """
        return 1 / (1 + np.exp(-y))

    @staticmethod
    def invlogitlogjac(y):
        """
        Computes the log jacobian for the inverse logit transformation.
        """
        return (- y - 2 * np.log(1. + np.exp(-y))).sum()

    @staticmethod
    def probit(x):
        """
        Probit Transformation:
        For x bounded 0-1, y is unbounded; between -inf and inf
        """
        return sqrt(2.) * erfinv(2 * x - 1)

    @staticmethod
    def invprobit(y):
        """
        Inverse Probit Transformation
        For real-valued variable y, result x is bounded 0-1
        """
        return 0.5 * (1 + erf(y / sqrt(2.)))

    @staticmethod
    def invprobitlogjac(y):
        """ Computes the log jacobian for the inverse probit transformation """
        return (-0.5 * np.log(2 * pi) - y * y / 2.).sum()

    @staticmethod
    def parameter_trace_plot(sample_parameters, path):
        """ Plots the trace of the sampler """
        palette = plt.get_cmap('Set1')
        if len(sample_parameters.shape) == 1:
            n = sample_parameters.shape[0]
            plt.plot(range(n), sample_parameters, marker = '', linewidth = 1)
        else:
            n, d = sample_parameters.shape
            for i in range(d):
                plt.subplot(d, 1, i+1)
                plt.plot(range(n), sample_parameters[:,i], marker = '',
                        color = palette(i), linewidth = 1)
        #plt.show()
        plt.savefig(path)
        return

    def parameter_pairwise_plot(self, sample_parameters, path):
        """ """
        def off_diag(x, y, **kwargs):
            plt.scatter(x, y, **kwargs)
            ax = plt.gca()
            ax.set_xlim(0,1)
            ax.set_ylim(0,1)
            return

        def on_diag(x, **kwargs):
            sea.distplot(x, bins = 20, kde = False, rug = True, **kwargs)
            ax = plt.gca()
            ax.set_xlim(0,1)
            return

        def uniquify(df_columns):
            seen = set()
            for item in df_columns:
                fudge = 1
                newitem = item

                while newitem in seen:
                    fudge += 1
                    newitem = "{}_{}".format(item, fudge)

                yield newitem
                seen.add(newitem)

        d = sample_parameters.shape[1]
        df = pd.DataFrame(sample_parameters, columns = self.parameter_order)
        df.columns = list(uniquify(df.columns))
        g = sea.PairGrid(df)
        g = g.map_offdiag(off_diag, s = 1)
        g = g.map_diag(on_diag)

        for i in range(d):
            g.axes[i,i].annotate(self.parameter_order[i], xy = (0.05, 0.9))
        for ax in g.axes.flatten():
            ax.set_ylabel('')
            ax.set_xlabel('')

        #plt.show()
        plt.savefig(path)
        return

class Chain(Transformer):
    N = 0
    s2_a = 0.1; s2_b = 0.1
    mu = 0
    ps2 = 4
    temperature = 1.
    rank = 0  # placeholder for MPI rank

    @property
    def curr_theta(self):
        return self.s_theta[self.iter]

    @property
    def curr_sigma2(self):
        return self.s_sigma2[self.iter]

    @property
    def curr_sse(self):
        return self.sse(self.curr_theta)

    @property
    def last_sse(self):
        return self.sse(self.s_theta[self.iter - 1])

    def sample_theta0(self, theta, Sigma):
        theta_bar = theta.mean(axis = 0)
        SigL = cholesky(Sigma)
        Siginv = cho_solve(SigL)
        S0 = cho_solve(cholesky(self.N * Siginv + self.prior_theta0_Sinv))
        m0 = (
            self.N * theta_bar.T.dot(Siginv) +
            self.prior_theta0_mu.T.dot(self.prior_theta0_Sinv)
            ).dot(S0)
        S0L = cholesky(S0)
        theta0_try = m0 + S0L.dot(norm.rvs(size = self.d))
        while not self.check_constraints(theta0_try):
            theta0_try = m0 + S0L.dot(norm.rvs(size = self.d))
        return theta0_try

    def sample_Sigma(theta, theta0):
        """ Sample Sigma """
        psi0 = self.prior_Sigma_psi + (theta - theta0).T.dot(theta - theta0)
        nu0  = self.prior_Sigma_nu + self.N

        Sigma = invwishart.rvs(df = nu0, scale = psi0)
        return Sigma

    def log_posterior_para(self, theta0, sigma20, theta):
        # Compute the log-posterior for a given theta
        sse = ((theta - theta0) * (theta - theta0)).sum()
        ssd = ((theta0 - self.mu) * (theta0 - self.mu)).sum()

        ll = (
            - 0.5 * sse / sigma20
            - 0.5 * ssd / self.prior_sigma2
            )

        return ll / self.temperature

    def log_posterior_state(self, state):
        """ Computes the posterior for a given state, at the chain's temp. """
        # Contribution to log-posterior from subchains
        lps_sc = np.array([
            self.subchains[i].log_posterior_state(
                {'theta0'  : state['theta0'],  'theta'  : state['theta'][i],
                 'sigma20' : state['sigma20'], 'sigma2' : state['sigma2'][i],}
                )
            for i in range(len(self.subchains))
            ]).sum()
        # sum of squares between theta0 and prior mean
        ssd = ((state['theta0'] - self.mu) * (state['theta0'] - self.mu)).sum()
        lp = (
            - (0.5 * self.N + self.s2_a + 1) * log(state['sigma20'])
            - (0.5 * ssd + self.s2_b) / state['sigma20']
            )
        return lp

    def set_temperature(self, temperature):
        """ Set the tempering temperature """
        self.temperature = temperature
        self.radius = self.r0 * log(temperature + 1, 10)
        return

    def normalize(self, x):
        """ Transform real scale to 0-1 scale """
        return (x - self.bounds[:,0]) / (self.bounds[:,1] - self.bounds[:,0])

    def unnormalize(self, z):
        """ Transform 0-1 scale to real scale """
        return z * (self.bounds[:,1] - self.bounds[:,0]) + self.bounds[:,0]

    def sse(self, parameters):
        """ computes sse between subchain and chain """
        theta = np.array([subchain.curr_theta for subchain in self.subchains])
        tdiff = theta - parameters
        return (tdiff * tdiff).sum()

    def log_posterior(self, sse, s2, ssd, ps2, temp = 1.):
        lp = (
            - 0.5 * (self.N / temp + self.s2_a) * log(s2)
            - 0.5 * (sse / s2 + ssd / self.ps2) / temp
            )
        return lp

    def sample_sigma2(self):
        """ Sample Sigma2 for the pooled parameter set """
        aa = 0.5 * self.N / self.temperature + self.s2_a
        bb = 0.5 * self.last_sse / self.temperature + self.s2_b
        return invgamma.rvs(aa, scale = bb)

    def localcov(self, target):
        lc = localcov(
            self.s_theta[:(self.iter - 1)], target,
            self.radius, self.nu, self.psi0
            )
        return lc

    def check_constraints(self, theta):
        """ Check the Material Model constraints """
        th = self.unnormalize(self.invprobit(theta))
        self.materialmodel.update_parameters(th)
        return self.materialmodel.check_constraints()

    def iter_sample(self):
        """ Pushes the sampler one iteration """
        # Push each subchain one iteration
        for subchain in self.subchains:
            subchain.iter_sample()

        # Start iteration for main chain
        self.iter += 1
        
        # Generate new sigma^2
        self.s_sigma2[self.iter] = self.sample_sigma2()

        # Setting up Covariance Matrix at current
        curr_theta = self.s_theta[self.iter - 1]
        curr_cov = self.localcov(curr_theta)
        curr_theta_diff = curr_theta - self.mu
        curr_ssd = sum(curr_theta_diff * curr_theta_diff)

        # Generating Proposal, computing Covariance Matrix at proposal
        S = cholesky(curr_cov)

        prop_theta = curr_theta + normal(size = self.d).dot(S)
        if not self.check_constraints(prop_theta):
            # If proposal violates constraints, don't bother calculating
            # log-posteriors, or proposal SSE's, etc.  Immediately do logic as
            # if failed the proposal.
            self.s_theta[self.iter] = curr_theta
            self.curr_sse = self.probit_sse(curr_theta)
            return

        prop_cov = self.localcov(prop_theta)
        prop_sse = self.sse(prop_theta)
        prop_theta_diff = prop_theta - self.mu
        prop_ssd = sum(prop_theta_diff * prop_theta_diff)
        prop_ldj = self.invprobitlogjac(prop_theta)

        # Log-Posterior for theta at current and proposal
        curr_lp = self.log_posterior(
                self.curr_sse,
                self.curr_sigma2,
                curr_ssd,
                self.ps2,
                self.temperature,
                )
        prop_lp = self.log_posterior(
                prop_sse,
                self.curr_sigma2,
                prop_ssd,
                self.ps2,
                self.temperature,
                )

        # Log-density of proposal dist from current to proposal (and visa versa)
        cp_ld = mvnorm.logpdf(prop_theta, curr_theta, curr_cov)
        pc_ld = mvnorm.logpdf(curr_theta, prop_theta, prop_cov)

        # Compute alpha
        log_alpha = prop_lp + pc_ld - curr_lp - cp_ld

        # Conduct Metropolis step:
        if log(uniform()) < log_alpha:
            self.accepted[self.iter] = 1
            self.s_theta[self.iter] = prop_theta
            self.curr_ldj = prop_ldj
        else:
            self.s_theta[self.iter] = curr_theta
            self.curr_sse = self.probit_sse(curr_theta)
        return

    def sample(self, ns):
        for _ in range(ns):
            self.iter_sample()
        return

    def get_state(self):
        """ Extracts the current state of the chain, returns it as a dictionary.
        Expecting to send it as a pickled object back to the host process. """

        sc_states = [subchain.get_state() for subchain in self.subchains]
        thetas  = np.array([sc_state['theta'] for sc_state in sc_states])
        sigma2s = np.array([sc_state['sigma2'] for sc_state in sc_states])
        sses    = np.array([subchain.curr_sse for subchain in self.subchains])
        ldjs    = np.array([sc_state['ldj'] for sc_state in sc_states])
        sse0    = sum((thetas - self.curr_theta) * (thetas - self.curr_theta))

        state = {
            'theta0'  : self.curr_theta,
            'sigma20' : self.curr_sigma2,
            'sse0'    : sse0,
            'theta'   : thetas,
            'sigma2'  : sigma2s,
            'sse'     : sses,
            'ldj'     : ldjs,
            'temp'    : self.temperature,
            'rank'    : self.rank,
            }
        return state

    def set_state(self, state):
        self.s_theta[self.iter] = state['theta0']
        self.s_sigma2[self.iter] = state['sigma20']
        self.curr_sse = state['sse0']
        for i in range(len(self.subchains)):
            self.subchains[i].set_state(state['theta'][i], state['sigma2'][i],
                                        state['sse'][i],   state['ldj'][i]    )
        return

    def get_history(self, nburn = 0, thin = 1):
        theta = self.invprobit(self.s_theta[(nburn + 1)::thin])
        s2    = self.s_sigma2[(nburn + 1)::thin]
        return (theta, s2)

    def initialize_sampler(self, ns):
        self.s_theta  = np.empty((ns, self.d))
        self.s_sigma2 = np.empty(ns)
        self.accepted = np.zeros(ns)
        self.iter     = 0

        for subchain in self.subchains:
            subchain.initialize_sampler(ns)

        try_theta = normal(scale = 1e-3, size = self.d)
        while not self.check_constraints(try_theta):
            try_theta = normal(scale = 1e-3, size = self.d)

        self.s_theta[0]  = try_theta
        self.s_sigma2[0] = 1e-5
        self.curr_ldj    = self.invprobitlogjac(self.curr_theta)
        return

    def __init__(self, xps, bounds, constants, nu = 40, psi0 = 1e-4,
                    r0 = 0.5, temp = 1., **kwargs):
        """
        Initialization of Hopkinson Bar Model.

        xp     - dictionary of inputs to transport class (HB)
        bounds - boundaries of parameters
        consts - dictionary of constant Values
        nu     - prior weighting for sampling covariance Matrix
        psi0   - prior diagonal for sampling covariance Matrix
        r0     - search radius for sampling covariance matrix (modified by temp)
        temp   - tempering temperature
        kwargs - Additional arguments to go to MaterialModel
        """
        self.materialmodel = MaterialModel(**kwargs)
        self.subchains = [
            SubChainHB(parent = self, xp = xp, temp = temp,
                        bounds = bounds, constants = constants,
                        nu = nu, psi0 = psi0, r0 = r0, **kwargs)
            for xp in xps
            ]
        self.nu = nu
        self.psi0 = psi0
        self.r0 = r0
        self.set_temperature(temp)

        self.N = len(self.subchains)
        self.n = np.array([subchain.N for subchain in self.subchains])

        self.parameter_order = self.materialmodel.get_parameter_list()
        self.d = len(self.parameter_order)

        self.bounds = np.array([bounds[key] for key in self.parameter_order])
        return

class ChainPlaceholder(Transformer):
    curr_theta = np.array([0.] * 8)
    curr_sigma2 = 0.25

    def __init__(self):
        return

class SubChainHB(Chain):
    """ A sub-chain conducts sampling of PTW parameters and error terms for a
    particular experiment.  Each experiment gets its own sub-chain.

    SubChainHB is developed for use with Hopkinson Bars and quasistatics.
    """
    N = 0
    s2_a = 100; s2_b = 5e-6
    temperature = 1.
    curr_sse = 1e10

    def log_posterior(self, sse, ldetjac, s2, ssd, ps2, temp = 1.):
        loglik = (
            - 0.5 * self.N * log(s2)
            - 0.5 * (sse / s2 + ssd / ps2)
            )
        return ldetjac + loglik / temp

    def log_posterior_para(self, theta, sigma2, theta0, sigma20):
        sse = self.probit_sse(theta)
        ssd = ((theta - theta0) * (theta - theta0)).sum()

        ldj = self.invprobitlogjac(theta)

        ll = (
            - 0.5 * sse / sigma2
            - 0.5 * ssd / sigma20
            )

        return ldj + ll / self.temperature

    def log_posterior_state(self, state):
        """ Computes the posterior for a given sub-chain
            state at the chain's temperature """
        lld = self.log_posterior_para(state['theta'],  state['sigma2'],
                                      state['theta0'], state['sigma20'])
        lpd = (
            - (0.5 * self.N / self.temperature + self.s2_a + 1) * log(state['sigma2'])
            - self.s2_b / state['sigma2']
            )
        return lld + lpd

    def initialize_submodel(self, params, consts):
        self.submodel.initialize_submodel(params, consts)
        return

    def sse(self, parameters):
        """ Calls submodel.sse for a given set of parameters. """
        # since submodel.sse is cached, the input needs to be hashable.
        # mutable objects (like numpy arrays) are not hashable, so translating
        # to tuple is necessary.
        return self.submodel.sse(tuple(parameters))

    def scaled_sse(self, normalized_parameters):
        """ de-scales the SSE from the 0-1 scale to the real-scale, passes
        on to sse """
        return self.sse(self.unnormalize(normalized_parameters))

    def probit_sse(self, probit_parameters):
        """ transforms parameters from probit scale, passes on to scaled_sse """
        return self.scaled_sse(self.invprobit(probit_parameters))

    def logit_sse(self, logit_parameters):
        """ transforms parameters from logit scale, passes on to scaled_sse """
        return self.scaled_sse(self.invlogit(logit_parameters))

    def iter_sample(self):
        """ Performs the sample step for 1 iteration. """
        self.iter += 1

        # Generate new sigma^2
        self.s_sigma2[self.iter] = self.sample_sigma2()

        # Setting up Covariance Matrix at current
        curr_theta = self.s_theta[self.iter - 1]
        curr_cov = self.localcov(curr_theta)
        curr_theta_diff = curr_theta - self.parent.curr_theta
        curr_ssd = sum(curr_theta_diff * curr_theta_diff)

        # Generating Proposal, computing Covariance Matrix at proposal
        prop_theta = curr_theta + normal(size = self.d).dot(cholesky(curr_cov))
        if not self.check_constraints(prop_theta):
            self.s_theta[self.iter] = curr_theta
            self.curr_sse = self.probit_sse(curr_theta)
            return

        prop_cov = self.localcov(prop_theta)
        prop_sse = self.probit_sse(prop_theta)
        prop_theta_diff = prop_theta - self.parent.curr_theta
        prop_ssd = sum(prop_theta_diff * prop_theta_diff)
        prop_ldj = self.invprobitlogjac(prop_theta)

        # Log-Posterior for theta at current and proposal
        curr_lp = self.log_posterior(
                self.curr_sse,
                self.curr_ldj,
                self.curr_sigma2,
                curr_ssd,
                self.parent.curr_sigma2,
                self.temperature,
                )
        prop_lp = self.log_posterior(
                prop_sse,
                prop_ldj,
                self.curr_sigma2,
                prop_ssd,
                self.parent.curr_sigma2,
                self.temperature,
                )

        # Log-density of proposal dist from current to proposal (and visa versa)
        cp_ld = mvnorm.logpdf(prop_theta, curr_theta, curr_cov)
        pc_ld = mvnorm.logpdf(curr_theta, prop_theta, prop_cov)

        # Compute alpha
        log_alpha = prop_lp + pc_ld - curr_lp - cp_ld

        # Conduct Metropolis step:
        if log(uniform()) < log_alpha:
            self.accepted[self.iter] = 1
            self.s_theta[self.iter] = prop_theta
            self.curr_sse = prop_sse
            self.curr_ldj = prop_ldj
        else:
            self.s_theta[self.iter] = curr_theta
            self.curr_sse = self.probit_sse(curr_theta)
        return

    def get_state(self):
        state = {
            'theta'  : self.curr_theta,
            'sigma2' : self.curr_sigma2,
            'sse'    : self.curr_sse,
            'ldj'    : self.curr_ldj,
            'temp'   : self.temperature,
            }
        return state

    def set_state(self, theta, s2, sse, ldj):
        self.s_theta[self.iter]  = theta
        self.s_sigma2[self.iter] = s2
        self.curr_sse = sse
        self.curr_ldj = ldj
        return

    def initialize_sampler(self, ns):
        self.s_theta  = np.empty((ns, self.d))
        self.s_sigma2 = np.empty(ns)
        self.accepted = np.zeros(ns)
        self.iter     = 0

        try_theta = normal(scale = 1e-2, size = self.d)
        while not self.check_constraints(try_theta):
            try_theta = normal(scale = 1e-2, size = self.d)

        self.s_theta[0]  = try_theta
        self.s_sigma2[0] = 1e-5
        self.curr_sse    = self.probit_sse(self.curr_theta)
        self.curr_ldj    = self.invprobitlogjac(self.curr_theta)
        return

    def __init__(
            self,
            parent,
            xp,
            bounds,
            constants,
            nu = 40,
            psi0 = 1e-4,
            r0 = 0.5,
            temp = 1.,
            **kwargs
            ):
        """ Initialization of Hopkinson Bar Model.

        xp     - dictionary of inputs to transport class (HB)
        bounds - boundaries of parameters
        consts - dictionary of constant Values
        nu     - prior weighting for sampling covariance Matrix
        psi0   - prior diagonal for sampling covariance Matrix
        r0     - search radius for sampling covariance matrix (modified by temp)
        temp   - tempering temperature
        kwargs - Additional arguments to go to MaterialModel
        """
        self.parent = parent

        self.nu = nu
        self.psi0 = psi0
        self.r0 = r0
        self.set_temperature(temp)

        self.submodel = SubModelHB(TransportHB(**xp), **kwargs)
        self.N = self.submodel.N
        self.parameter_order = self.submodel.parameter_order
        self.d = len(self.parameter_order)

        # Expose the submodel's materialmodel object and methods (check constraint)
        self.materialmodel = self.submodel.model

        self.bounds = np.array([bounds[key] for key in self.parameter_order])

        params = {
            x : y
            for x, y in
            zip(self.parameter_order,np.zeros(self.d))
            }
        self.initialize_submodel(params, constants)
        return

class ParallelTemperMaster(Transformer):
    temp_ladder = np.array([1.])
    chains = []

    swap_yes = []
    swap_no  = []

    def initialize_chains(self, kwargs):
        self.chains = [Chain(**kwargs, temp = t) for t in self.temp_ladder]
        return

    def get_states(self):
        states = [chain.get_state() for chain in self.chains]
        return states

    def swap_state(self, state_1, state_2):
        rank_1 = state_1['rank'] - 1
        rank_2 = state_2['rank'] - 1
        self.chains[rank_1].set_state(state_2)
        self.chains[rank_2].set_state(state_1)
        pass

    @staticmethod
    def log_posterior(state, temp):
        lp = (
            - 0.5 * (state['n'] / temp + state['s2_a'] + 1) * log(state['sigma2'])
            - 0.5 * sum((state['sse'] / temp + state['s2_b']) / state['sigma2'])
            - 0.5 * (state['N'] / temp + state['ps2_a']) + 1
            - 0.5 * ((state['sse0'] / temp + self.s2_b) / state['sigma20'])
            )
        return lp

    def log_alpha(state_a, state_b):
        lpxx = self.log_posterior(x, x['temp'])
        lpyy = self.log_posterior(y, y['temp'])
        lpxy = self.log_posterior(x, y['temp'])
        lpyx = self.log_posterior(y, x['temp'])
        return lpxy + lpyx - lpxx - lpyy

    def try_swap_states(self):
        states = self.get_states()
        shuffle(states)

        for x, y in list(zip(states[::2], states[1::2])):
            if log(uniform()) < self.log_alpha(x,y):
                self.swap_state(x,y)
                self.swap_yes.append((x['rank'], y['rank']))
            else:
                self.swap_no.append((x['rank'], y['rank']))
        return

    def sample(self, ns):
        for chain in self.chains:
            chain.sample(ns)
        return

    def __init__(self, **kwargs):
        self.size = size
        self.temp_ladder = temp_ladder

        self.initialize_chains(kwargs)
        self.set_chain_temperatures()
        return

if __name__ == '__main__':
    pass

# EOF
