"""
statistical_models_hier_redux.py

Defines the Statistical models

Chain - Defines the
SubChain -
"""
import numpy as np
import pandas as pd

# from mpi4py import MPI
from scipy.stats import invgamma, multivariate_normal as mvnorm
from scipy.special import erf, erfinv
from numpy.linalg import cholesky
from numpy.random import normal, uniform
from random import sample
from math import ceil, sqrt, pi, log, exp
from itertools import combinations
from pointcloud import localcov
from random import shuffle

from submodel import SubModelHB, SubModelTC, SubModelFP
from transport import TransportHB, TransportTC, TransportFP

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

    @staticmethod
    def parameter_pairwise_plot(sample_parameters, path):
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

class Chain(object, Transformer):
    N = 0
    s2_a = 1; s2_b = 1
    temperature = 1.

    curr_sse = 1e10

    @property
    def curr_theta(self):
        return self.s_theta[self.iter]

    @property
    def curr_s2(self):
        return self.s_sigma2[self.iter]

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
        tdiff = theta - self.curr_theta
        return sum(tdiff * tdiff)

    def log_posterior(self, sse, ldetjac, s2, temp = self.temperature):
        loglik = ()
        logpri = ()
        return logpri + ldetjac + loglik / temp

    def sample_sigma2(self):
        """ Sample Sigma2 for the pooled parameter set """
        aa = 0.5 * self.N / self.temperature + self.s2_a
        bb = 0.5 * self.curr_sse / self.temperature + self.s2_b
        return invgamma.rvs(aa, scale = b)

    def localcov(self, target):
        lc = localcov(
            self.s_theta[:(self.iter - 1)], target,
            self.radius, self.nu, self.psi0
            )
        return lc

    def get_state(self):
        """ Extracts the current state of the chain, returns it as a dictionary.
        Expecting to send it as a pickled object back to the host process. """

        sc_states = [subchain.get_state() for subchain in self.subchains]
        thetas  = np.array([sc_state['theta'] for sc_state in sc_states])
        sigma2s = np.array([sc_state['sigma2'] for sc_state in sc_states])
        sses    = np.array([subchain.curr_sse for subchain in self.subchains])
        sse0    = sum((thetas - self.curr_theta) * (thetas - self.curr_theta))

        state = {
            'theta0' : self.curr_theta,
            'sigma20' : self.curr_sigma2,
            'sse0'    : sse0,
            'theta'   : thetas,
            'sigma2'  : sigma2s,
            'sse'     : sses,
            'temp'    : self.temperature,
            }
        return state

    def get_history(self, nburn = 0, thin = 1):
        theta = self.invprobit(self.s_theta[(nburn + 1)::thin])
        s2    = self.s_s2[(nburn + 1)::thin]
        return (theta, s2)

    def initialize_sampler(self, ns, starting):
        self.s_theta  = np.empty((ns, self.d))
        self.s_s2     = np.empty(ns)
        self.accepted = np.zeros(ns)
        self.iter     = 0

        for subchain in self.subchains:
            subchain.initialize_sampler(ns, starting)

        self.s_theta[0] = starting
        self.s_s2[0]    = 1e-5
        self.curr_sse   = self.probit_sse(self.curr_theta)
        self.curr_ldj   = self.invprobitlogjac(self.curr_theta)
        return

    def __init__(self, ):
        pass

class SubChainHB(Chain):
    """ A sub-chain conducts sampling of PTW parameters and error terms for a
    particular experiment.  Each experiment gets its own sub-chain.

    SubChainHB is developed for use with Hopkinson Bars and quasistatics.
    """
    N = 0
    s2_a = 100; s2_b = 5e-6
    temperature = 1.
    curr_sse = 1e10

    def log_posterior(self, sse, ldetjac, s2, ssd, ps2, temp = self.temperature):
        loglik = (
            - 0.5 * self.N * log(s2)
            - 0.5 * (sse / s2 + ssd / ps2)
            )

        # logpri = (
        #    - (self.s2_a - 1) * log(s2)
        #    - self.s2_b / s2
        #    )

        logpri = 0

        return logpri + ldetjac + loglik / temp

    def initialize_submodel(self, params, consts):
        self.submodel.initialize_model(params, consts)
        return

    def sse(self, parameters):
        return self.submodel.sse(parameters)

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
        self.iter += 1

        # Generate new sigma^2
        self.s_s2[self.iter] = self.sample_sigma2()

        # Setting up Covariance Matrix at current
        curr_theta = self.s_theta[self.iter - 1]
        curr_cov = self.localcov(curr_theta)
        curr_theta_diff = curr_theta - self.parent.curr_theta
        curr_ssd = sum(curr_theta_diff * curr_theta_diff)

        # Generating Proposal, computing Covariance Matrix at proposal
        prop_theta = curr_theta + normal(size = self.d).dot(cholesky(curr_cov))
        prop_cov = self.localcov(prop_theta)
        prop_sse = self.probit_sse(prop_theta)
        prop_theta_diff = prop_theta - self.parent.curr_theta
        prop_ssd = sum(prop_theta_diff * prop_theta_diff)
        prop_ldj = self.invprobitlogjac(prop_theta)

        # Log-Posterior for theta at current and proposal
        curr_lp = self.log_posterior(
                self.curr_sse,
                self.curr_ldj,
                self.curr_s2,
                curr_ssd,
                self.parent.curr_s2,
                )
        prop_lp = self.log_posterior(
                prop_sse,
                prop_ldj,
                self.curr_s2,
                prop_ssd,
                self.parent.curr_s2,
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

    def sample(self, n):
        for _ in range(n):
            self.iter_sample()
        return

    def get_state(self):
        state = {
            'theta' : self.curr_theta,
            's2'    : self.curr_s2,
            'sse'   : self.curr_sse,
            'ldj'   : self.curr_ldj,
            'temp'  : self.temperature,
            }
        return state

    def initialize_sampler(self, ns, starting):
        self.s_theta  = np.empty((ns, self.d))
        self.s_s2     = np.empty(ns)
        self.accepted = np.zeros(ns)
        self.iter     = 0

        self.s_theta[0] = starting
        self.s_s2[0]    = 1e-5
        self.curr_sse   = self.probit_sse(self.curr_theta)
        self.curr_ldj   = self.invprobitlogjac(self.curr_theta)
        return

    def __init__(
            self,
            xp,
            bounds,
            consts,
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
        self.nu = nu
        self.psi0 = psi0
        self.r0 = r0
        self.set_temperature(temp)

        self.submodel = SubModelHB(TransportHB(**xp), **kwargs)
        self.N = self.submodel.N

        self.set_parameter_order()
        self.d = len(self.parameter_order)

        self.bounds = np.array([bounds[key] for key in self.parameter_order])

        params = {
            x : y
            for x, y in zip(
                self.parameter_order,
                self.unnormalize(np.ones(self.d) * 0.5)
                )
            }
        self.initialize_submodel(params, consts)
        return

if __name__ == '__main__':
    pass

# EOF
