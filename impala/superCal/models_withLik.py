######################################
######################################
### Impala Model Class Definitions ###
###################################### 
######################################

###############
### Imports ###
###############
import numpy as np
import pyBASS as pb
#import pyBayesPPR as pbppr
#import physical_models_vec as pm_vec
from impala import physics as pm_vec
from itertools import cycle
from scipy.interpolate import interp1d
from scipy.linalg import cho_factor, cho_solve, cholesky
import scipy.linalg.lapack as la
import abc

########################
### Helper Functions ###
########################

def cor2cov(R, s): # R is correlation matrix, s is sd vector
    return(np.outer(s, s) * R)

def chol_sample(mean, cov):
    return mean + np.dot(np.linalg.cholesky(cov), np.random.standard_normal(mean.size))


#####################
### Model Classes ### #should have eval method and stochastic attribute
#####################

#######
### AbstractModel: Internal class, not called by users
class AbstractModel:
    """
    Base Class for Simulator/Emulator Models.  
    """
    def __init__(self):
        pass
    
    @abc.abstractmethod
    def eval(self, parmat): # this must be implemented for each model type
        pass
    #@profile
    def llik(self, yobs, pred, cov): # assumes diagonal cov
        vec = yobs - pred
        vec2 = vec*vec*cov['inv']
        out = -.5 * cov['ldet'] - .5 * vec2.sum()
        return out
    #@profile
    def lik_cov_inv(self, s2vec): # default is diagonal covariance matrix
        inv = 1/s2vec
        ldet = np.log(s2vec).sum()
        out = {'inv' : inv, 'ldet' : ldet}
        return out

    def step(self):
        return

#######
### ModelBassPca_mult: Model with BASS Emulator from pyBASS with Multivariate Output
class ModelBassPca_mult(AbstractModel):
    """ 
    ModelBassPca_mult: PCA Based BASS Model Emulator for Multivariate Outputs
    
    ModelBassPca_mult is not recommended for larger-dimensional functional outputs. Instead, use ModelBassPca_func.
    """
    def __init__(self, bmod, input_names, exp_ind=None, s2='MH'): 
        """
        bmod        : bassPCA fit 
        input_names : list of the names of the inputs to bmod
        s2          : method for handling experiment-specific noise s2; options are 'MH' (Metropolis-Hastings Sampling), 'fix' (fixed at s2_est from addVecExperiments call)
        """
        self.mod = bmod
        self.stochastic = True
        self.nmcmc = len(bmod.bm_list[0].samples.s2)
        self.input_names = input_names
        self.trunc_error_cov = np.cov(self.mod.trunc_error)
        self.basis = self.mod.basis
        self.meas_error_cor = np.eye(self.basis.shape[0])
        self.discrep_cov = np.eye(self.basis.shape[0])*1e-12
        self.ii = 0
        npc = self.mod.nbasis
        self.mod_s2 = np.empty([self.nmcmc, npc])
        for i in range(npc):
            self.mod_s2[:,i] = self.mod.bm_list[i].samples.s2
        self.emu_vars = self.mod_s2[self.ii]
        self.yobs = None
        self.marg_lik_cov = None
        self.nd = 0
        if exp_ind is None:
            exp_ind = np.array(0)
        self.nexp = exp_ind.max() + 1
        self.exp_ind = exp_ind
        self.s2 = s2
        if s2=='gibbs':
            raise "Cannot use Gibbs s2 for emulator models."
        return

    def step(self):
        self.ii = np.random.choice(range(self.nmcmc), 1).item()
        self.emu_vars = self.mod_s2[self.ii]
        return

    def eval(self, parmat, pool = None, nugget=False):
        """
        parmat : ~
        """
        parmat_array = np.vstack([parmat[v] for v in self.input_names]).T # get correct subset/ordering of inputs
        pred = self.mod.predict(parmat_array, mcmc_use=np.array([self.ii]), nugget=nugget)[0, :, :]

        if pool is True:
            return pred
        else:
            nrep = list(parmat.values())[0].shape[0] // self.nexp
            return np.concatenate([pred[np.ix_(np.arange(i, nrep*self.nexp, self.nexp), np.where(self.exp_ind==i)[0])] for i in range(self.nexp)], 1)
            # this is evaluating all experiments for all thetas, which is overkill

    def llik(self, yobs, pred, cov):
        vec = yobs - pred 
        out = -.5*(cov['ldet'] + vec.T @ cov['inv'] @ vec)
        return out

    def lik_cov_inv(self, s2vec):
        n = len(s2vec)
        Sigma = cor2cov(self.meas_error_cor[:n,:n], np.sqrt(s2vec)) # :n is a hack for when ntheta>1 in heir...fix this sometime
        mat = Sigma + self.trunc_error_cov + self.discrep_cov + self.basis @ np.diag(self.emu_vars) @ self.basis.T
        # this doesnt work for vectorized experiments...maybe dont allow those for BASS
        chol = cholesky(mat)
        ldet = 2 * np.sum(np.log(np.diag(chol)))
        #la.dpotri(chol, overwrite_c=True) # overwrites chol with original matrix inverse
        inv=np.linalg.inv(mat)
        out = {'inv' : inv, 'ldet' : ldet}
        return out


#######
### ModelBpprPca_mult: Model with BayesPPR Emulator with Multivariate Output
### Not recommended for larger-dimensional functional outputs: instead, use ModelBpprPca_func
class ModelBpprPca_mult(AbstractModel):
    """ 
    ModelBpprPca_mult: BayesPPR Model Emulator for Multivariate Outputs
    
    ModelBpprPca_mult is not recommended for larger-dimensional functional outputs. Instead, use ModelBpprPca_func.
    """
    def __init__(self, bmod, input_names, exp_ind=None, s2='MH'):
        """
        bmod        : bassPCA fit 
        input_names : list of the names of the inputs to bmod
        s2          : method for handling experiment-specific noise s2; options are 'MH' (Metropolis-Hastings Sampling), 'fix' (fixed at s2_est from addVecExperiments call)
        """
        self.mod = bmod
        self.stochastic = True
        self.nmcmc = len(bmod.bm_list[0].samples.sdResid)
        self.input_names = input_names
        self.trunc_error_cov = np.cov(self.mod.trunc_error)
        self.basis = self.mod.basis
        self.meas_error_cor = np.eye(self.basis.shape[0])
        self.discrep_cov = np.eye(self.basis.shape[0])*1e-12
        self.ii = 0
        npc = self.mod.nbasis
        self.mod_s2 = np.empty([self.nmcmc, npc])
        for i in range(npc):
            self.mod_s2[:,i] = self.mod.bm_list[i].samples.sdResid**2
        self.emu_vars = self.mod_s2[self.ii]
        self.yobs = None
        self.marg_lik_cov = None
        self.nd = 0
        if exp_ind is None:
            exp_ind = np.array(0)
        self.nexp = exp_ind.max() + 1
        self.exp_ind = exp_ind
        self.s2 = s2
        if s2=='gibbs':
            raise "Cannot use Gibbs s2 for emulator models."
        return

    def step(self):
        self.ii = np.random.choice(range(self.nmcmc), 1).item()
        self.emu_vars = self.mod_s2[self.ii]
        return

    def eval(self, parmat, pool = None, nugget=False):
        """
        parmat : ~
        """
        parmat_array = np.vstack([parmat[v] for v in self.input_names]).T # get correct subset/ordering of inputs
        pred = self.mod.predict(parmat_array, mcmc_use=np.array([self.ii]), nugget=nugget)[0, :, :]

        if pool is True:
            return pred
        else:
            nrep = list(parmat.values())[0].shape[0] // self.nexp
            return np.concatenate([pred[np.ix_(np.arange(i, nrep*self.nexp, self.nexp), np.where(self.exp_ind==i)[0])] for i in range(self.nexp)], 1)
            # this is evaluating all experiments for all thetas, which is overkill

    def llik(self, yobs, pred, cov):
        vec = yobs - pred 
        out = -.5*(cov['ldet'] + vec.T @ cov['inv'] @ vec)
        return out

    def lik_cov_inv(self, s2vec):
        n = len(s2vec)
        Sigma = cor2cov(self.meas_error_cor[:n,:n], np.sqrt(s2vec)) # :n is a hack for when ntheta>1 in heir...fix this sometime
        mat = Sigma + self.trunc_error_cov + self.discrep_cov + self.basis @ np.diag(self.emu_vars) @ self.basis.T
        # this doesnt work for vectorized experiments...maybe dont allow those for BASS
        chol = cholesky(mat)
        ldet = 2 * np.sum(np.log(np.diag(chol)))
        #la.dpotri(chol, overwrite_c=True) # overwrites chol with original matrix inverse
        inv=np.linalg.inv(mat)
        out = {'inv' : inv, 'ldet' : ldet}
        return out


#######
### ModelBassPca_func: Model with BASS Emulator from pyBASS with Functional Response
class ModelBassPca_func(AbstractModel):
    """ 
    ModelBassPca_func: PCA Based BASS Model Emulator for Functional Outputs
    
    ModelBassPca_func Handles larger-dimensional functional responses (e.g., on large spatial fields) using
    various inversion tricks. We require any other covariance e.g., from discrepancy, measurement error, and basis truncation error)
    to be diagnonal. Smaller-dimensional functional responses could be specified with non-diagonal covariances using ModeBassPca_mult.
    """
    def __init__(self, bmod, input_names, exp_ind=None, s2='MH'):
        """
        bmod        : bassPCA fit 
        input_names : list of the names of the inputs to bmod
        s2          : method for handling experiment-specific noise s2; options are 'MH' (Metropolis-Hastings Sampling), 'fix' (fixed at s2_est from addVecExperiments call)
        """
        self.mod = bmod
        self.stochastic = True
        self.nmcmc = len(bmod.bm_list[0].samples.s2)
        self.input_names = input_names
        self.basis = self.mod.basis
        self.meas_error_cor = np.eye(self.basis.shape[0])
        self.discrep_cov = np.eye(self.basis.shape[0])*1e-12
        self.ii = 0
        npc = self.mod.nbasis
        if npc > 1:
            self.trunc_error_var = np.diag(np.cov(self.mod.trunc_error))
        else:
            self.trunc_error_var = np.diag(np.cov(self.mod.trunc_error).reshape([1,1]))
        self.mod_s2 = np.empty([self.nmcmc, npc])
        for i in range(npc):
            self.mod_s2[:,i] = self.mod.bm_list[i].samples.s2
        self.emu_vars = self.mod_s2[self.ii]
        self.yobs = None
        self.marg_lik_cov = None
        self.discrep_vars = None
        self.nd = 0
        self.discrep_tau = 1.
        self.D = None
        self.discrep = 0.
        if exp_ind is None:
            exp_ind = np.array(0)
        self.nexp = exp_ind.max() + 1
        self.exp_ind = exp_ind
        self.s2 = s2
        if s2=='gibbs':
            raise "Cannot use Gibbs s2 for emulator models."
        return

    def step(self):
        self.ii = np.random.choice(range(self.nmcmc), 1).item()
        self.emu_vars = self.mod_s2[self.ii]
        return
    #@profile
    def discrep_sample(self, yobs, pred, cov, itemp):
        #if self.nd>0:
        S = np.linalg.inv(
            np.eye(self.nd) / self.discrep_tau 
            + self.D.T @ cov['inv'] @ self.D
            )
        m = self.D.T @ cov['inv'] @ (yobs - pred)
        discrep_vars = chol_sample(S @ m, S/itemp)
        #self.discrep = self.D @ self.discrep_vars
        return discrep_vars
    #@profile
    def eval(self, parmat, pool = None, nugget=False):
        """
        parmat : ~
        """
        parmat_array = np.vstack([parmat[v] for v in self.input_names]).T # get correct subset/ordering of inputs
        pred = self.mod.predict(parmat_array, mcmc_use=np.array([self.ii]), nugget=nugget)[0, :, :]

        if pool is True:
            return pred
        else:
            nrep = list(parmat.values())[0].shape[0] // self.nexp
            return np.concatenate([pred[np.ix_(np.arange(i, nrep*self.nexp, self.nexp), np.where(self.exp_ind==i)[0])] for i in range(self.nexp)], 1)
            # this is evaluating all experiments for all thetas, which is overkill

    #@profile
    def llik(self, yobs, pred, cov):
        vec = yobs - pred 
        out = -.5*(cov['ldet'] + vec.T @ cov['inv'] @ vec)
        return out
    #@profile
    def lik_cov_inv(self, s2vec):
        vec = self.trunc_error_var + s2vec
        #mat = np.diag(vec) + self.basis @ np.diag(self.emu_vars) @ self.basis.T
        #inv = np.linalg.inv(mat)
        #ldet = np.linalg.slogdet(mat)[1]
        #out = {'inv' : inv, 'ldet' : ldet}
        Ainv = np.diag(1/vec)
        Aldet = np.log(vec).sum()
        out = self.swm(Ainv, self.basis, np.diag(1/self.emu_vars), self.basis.T, Aldet, np.log(self.emu_vars).sum())
        return out
    #@profile
    def chol_solve(self, x):
        mat = cho_factor(x)
        ldet = 2 * np.sum(np.log(np.diag(mat[0])))
        ##la.dpotri(mat, overwrite_c=True) # overwrites mat with original matrix inverse, but not correct
        #inv = cho_solve(mat, np.eye(x.shape[0])) # correct, but slower for small dimension
        inv = np.linalg.inv(x)
        out = {'inv' : inv, 'ldet' : ldet}
        return out
    #@profile
    def swm(self, Ainv, U, Cinv, V, Aldet, Cldet): # sherman woodbury morrison (A+UCV)^-1 and |A+UCV|
        in_mat = self.chol_solve(Cinv + V @ Ainv @ U)
        inv = Ainv - Ainv @ U @ in_mat['inv'] @ V @ Ainv
        ldet = in_mat['ldet'] + Aldet + Cldet
        out = {'inv' : inv, 'ldet' : ldet}
        return out


#######
### ModelBpprPca_func: Model with BayesPPR Emulator with Functional Responses
class ModelBpprPca_func(AbstractModel):
    """ 
    ModelBpprPca_func: BayesPPR Model Emulator for Functional Outputs
    
    ModelBpprPca_func Handles larger-dimensional functional responses (e.g., on large spatial fields) using
    various inversion tricks. We require any other covariance e.g., from discrepancy, measurement error, and basis truncation error)
    to be diagnonal. Smaller-dimensional functional responses could be specified with non-diagonal covariances using ModelBpprPca_mult.
    """
    def __init__(self, bmod, input_names, exp_ind=None, s2='MH'):
        """
        bmod        : bassPCA fit 
        input_names : list of the names of the inputs to bmod
        s2          : method for handling experiment-specific noise s2; options are 'MH' (Metropolis-Hastings Sampling), 'fix' (fixed at s2_est from addVecExperiments call)
        """
        self.mod = bmod
        self.stochastic = True
        self.nmcmc = len(bmod.bm_list[0].samples.sdResid)
        self.input_names = input_names
        self.basis = self.mod.basis
        self.meas_error_cor = np.eye(self.basis.shape[0])
        self.discrep_cov = np.eye(self.basis.shape[0])*1e-12
        self.ii = 0
        npc = self.mod.nbasis
        if npc > 1:
            self.trunc_error_var = np.diag(np.cov(self.mod.trunc_error))
        else:
            self.trunc_error_var = np.diag(np.cov(self.mod.trunc_error).reshape([1,1]))
        self.mod_s2 = np.empty([self.nmcmc, npc])
        for i in range(npc):
            self.mod_s2[:,i] = self.mod.bm_list[i].samples.sdResid**2
        self.emu_vars = self.mod_s2[self.ii]
        self.yobs = None
        self.marg_lik_cov = None
        self.discrep_vars = None
        self.nd = 0
        self.discrep_tau = 1.
        self.D = None
        self.discrep = 0.
        if exp_ind is None:
            exp_ind = np.array(0)
        self.nexp = exp_ind.max() + 1
        self.exp_ind = exp_ind
        self.s2 = s2
        if s2=='gibbs':
            raise "Cannot use Gibbs s2 for emulator models."
        return

    def step(self):
        self.ii = np.random.choice(range(self.nmcmc), 1).item()
        self.emu_vars = self.mod_s2[self.ii]
        return
    #@profile
    def discrep_sample(self, yobs, pred, cov, itemp):
        #if self.nd>0:
        S = np.linalg.inv(
            np.eye(self.nd) / self.discrep_tau 
            + self.D.T @ cov['inv'] @ self.D
            )
        m = self.D.T @ cov['inv'] @ (yobs - pred)
        discrep_vars = chol_sample(S @ m, S/itemp)
        #self.discrep = self.D @ self.discrep_vars
        return discrep_vars
    #@profile
    def eval(self, parmat, pool = None, nugget=False):
        """
        parmat : ~
        """
        parmat_array = np.vstack([parmat[v] for v in self.input_names]).T # get correct subset/ordering of inputs
        pred = self.mod.predict(parmat_array, mcmc_use=np.array([self.ii]), nugget=nugget)[0, :, :]

        if pool is True:
            return pred
        else:
            nrep = list(parmat.values())[0].shape[0] // self.nexp
            return np.concatenate([pred[np.ix_(np.arange(i, nrep*self.nexp, self.nexp), np.where(self.exp_ind==i)[0])] for i in range(self.nexp)], 1)
            # this is evaluating all experiments for all thetas, which is overkill

    #@profile
    def llik(self, yobs, pred, cov):
        vec = yobs - pred 
        out = -.5*(cov['ldet'] + vec.T @ cov['inv'] @ vec)
        return out
    #@profile
    def lik_cov_inv(self, s2vec):
        vec = self.trunc_error_var + s2vec
        #mat = np.diag(vec) + self.basis @ np.diag(self.emu_vars) @ self.basis.T
        #inv = np.linalg.inv(mat)
        #ldet = np.linalg.slogdet(mat)[1]
        #out = {'inv' : inv, 'ldet' : ldet}
        Ainv = np.diag(1/vec)
        Aldet = np.log(vec).sum()
        out = self.swm(Ainv, self.basis, np.diag(1/self.emu_vars), self.basis.T, Aldet, np.log(self.emu_vars).sum())
        return out
    #@profile
    def chol_solve(self, x):
        mat = cho_factor(x)
        ldet = 2 * np.sum(np.log(np.diag(mat[0])))
        ##la.dpotri(mat, overwrite_c=True) # overwrites mat with original matrix inverse, but not correct
        #inv = cho_solve(mat, np.eye(x.shape[0])) # correct, but slower for small dimension
        inv = np.linalg.inv(x)
        out = {'inv' : inv, 'ldet' : ldet}
        return out
    #@profile
    def swm(self, Ainv, U, Cinv, V, Aldet, Cldet): # sherman woodbury morrison (A+UCV)^-1 and |A+UCV|
        in_mat = self.chol_solve(Cinv + V @ Ainv @ U)
        inv = Ainv - Ainv @ U @ in_mat['inv'] @ V @ Ainv
        ldet = in_mat['ldet'] + Aldet + Cldet
        out = {'inv' : inv, 'ldet' : ldet}
        return out




#######
### ModelF: Function for Simulator Model Evaluation or Evaluation of Alternative Emulator Model
class ModelF(AbstractModel):
    """ Custom Simulator/Emulator Model """
    def __init__(self, f, input_names, exp_ind=None, s2='gibbs'): # not sure if this is vectorized
        """
        f           : user-defined function taking single input with elements x[0] = first element of theta, x[1] = second element of theta, etc. Function must output predictions for all observations        
        input_names : list of the names of the inputs to bmod
        s2          : method for handling experiment-specific noise s2; options are 'MH' (Metropolis-Hastings Sampling), 'fix' (fixed at s2_est from addVecExperiments call), and 'gibbs' (Gibbs sampling)
        """
        self.mod = f
        self.input_names = input_names
        self.stochastic = False
        self.yobs = None
        self.meas_error_cor = 1.#np.diag(self.basis.shape[0])
        if exp_ind is None:
            exp_ind = np.array(0)
        self.nexp = exp_ind.max() + 1
        self.exp_ind = exp_ind
        self.nd = 0
        self.s2 = s2
        return

    def eval(self, parmat, pool = None, nugget=False):
        parmat_array = np.vstack([parmat[v] for v in self.input_names]).T # get correct subset/ordering of inputs
        if pool is True:
            return np.apply_along_axis(self.mod, 1, parmat_array)
        else:
            nrep = list(parmat.values())[0].shape[0] // self.nexp
            out_all = np.apply_along_axis(self.mod, 1, parmat_array)
            
            #out_sub = np.concatenate([out_all[(i*nrep):(i*nrep+nrep), self.exp_ind==i] for i in range(self.nexp)], 1)
            out_sub = np.concatenate([out_all[np.ix_(np.arange(i, nrep*self.nexp, self.nexp), np.where(self.exp_ind==i)[0])] for i in range(self.nexp)], 1)
            return out_sub
            # this is evaluating all experiments for all thetas, which is overkill
        # need to have some way of dealing with non-pooled eval fo this and bassPCA version
        
    def discrep_sample(self, yobs, pred, cov, itemp): #Added by Lauren on 11/17/23.
        S = np.linalg.inv(
            np.eye(self.nd) / self.discrep_tau #defined by addVecExperiments
            + self.D.T @ (cov['inv'].flatten() * np.eye(len(yobs))) @ self.D
            )
        m = self.D.T @ (cov['inv'] * np.eye(len(yobs))) @ (yobs - pred)
        discrep_vars = sc.chol_sample(S @ m, S/itemp)
        return discrep_vars
    

#######
### ModelF_bigdata: Function for Simulator Model Evaluation or Evaluation of Alternative Emulator Model using Bigger Data 
class ModelF_bigdata(AbstractModel):
    """ Custom Simulator/Emulator Model """
    def __init__(self, f, input_names, exp_ind=None, s2='gibbs'): # not sure if this is vectorized
        """
        f           : user-defined function taking single input with elements x[0] = first element of theta, x[1] = second element of theta, etc. Function must output predictions for all observations        
        input_names : list of the names of the inputs to bmod
        s2          : method for handling experiment-specific noise s2; options are 'MH' (Metropolis-Hastings Sampling), 'fix' (fixed at s2_est from addVecExperiments call), and 'gibbs' (Gibbs sampling)
        """
        self.mod = f
        self.input_names = input_names
        self.stochastic = False
        self.yobs = None
        self.meas_error_cor = 1.#np.diag(self.basis.shape[0])
        if exp_ind is None:
            exp_ind = np.array(0)
        self.nexp = exp_ind.max() + 1
        self.exp_ind = exp_ind
        self.nd = 0
        self.s2 = s2
        self.vec = np.linspace(1,16200,16200) #define to speed up llik evaluation
        self.vec2 = np.linspace(1,16200,16200) #define to speed up llik evaluation
        self.inv = np.linspace(1,16200,16200) #define to speed up lik_cov_inv evaluation
        self.m = np.linspace(1,16200,16200) #define to speed up discrep_sample evaluation
        self.vmat = np.linspace(1,16200,16200) #define to speed up discrep_sample evaluation
        return
    def eval(self, parmat, pool = None, nugget=False):
        parmat_array = np.vstack([parmat[v] for v in self.input_names]).T # get correct subset/ordering of inputs
        if pool is True:
            return np.apply_along_axis(self.mod, 1, parmat_array)
        else:
            nrep = list(parmat.values())[0].shape[0] // self.nexp
            out_all = np.apply_along_axis(self.mod, 1, parmat_array)
            
            #out_sub = np.concatenate([out_all[(i*nrep):(i*nrep+nrep), self.exp_ind==i] for i in range(self.nexp)], 1)
            out_sub = np.concatenate([out_all[np.ix_(np.arange(i, nrep*self.nexp, self.nexp), np.where(self.exp_ind==i)[0])] for i in range(self.nexp)], 1)
            return out_sub
            # this is evaluating all experiments for all thetas, which is overkill
        # need to have some way of dealing with non-pooled eval fo this and bassPCA version  
    def discrep_sample(self, yobs, pred, cov, itemp): #Added by Lauren on 11/17/23.
        self.vec = cov['inv'].reshape(-1,1)* (yobs - pred).reshape(-1,1)
        self.vmat = np.repeat(cov['inv'].reshape(-1,1),self.nd,axis=1)*self.D
        S = np.linalg.inv(
            #np.eye(self.nd) / self.discrep_tau #defined by addVecExperiments
            np.diag(1/self.discrep_tau) #modification to allow vector-valued discrep_tau
           + self.D.T @ self.vmat
            )
        self.m = self.D.T @ self.vec
        discrep_vars = sc.chol_sample((S @ self.m).flatten(), S/itemp)
        return discrep_vars
    def llik(self, yobs, pred, cov): # assumes diagonal cov
        self.vec = yobs.flatten() - pred.flatten()
        self.vec2 = self.vec*self.vec*cov['inv']
        out = -.5 * cov['ldet'] - .5 * self.vec2.sum()
        return out
    def lik_cov_inv(self, s2vec): # default is diagonal covariance matrix
        self.inv = 1/s2vec
        ldet = np.log(s2vec).sum()
        out = {'inv' : self.inv, 'ldet' : ldet}
        return out



#######
### ModelMaterialStrength: PTW Model for Hopi-Bar / Quasistatic Experiments
epsilon = 1e-5 #used in ModelMaterialStrength to pad set of strains for evaluate for each experiment, user can ignore
class ModelMaterialStrength(AbstractModel):
    """ 
    PTW Model for Hoppy-Bar / Quasistatic Experiments 
    
    Currently not able to handle pooled model discrepancy.
    """
    def __init__(self, temps, edots, consts, strain_histories, flow_stress_model, melt_model, shear_model, specific_heat_model, density_model, pool=True, s2='gibbs'):
        """
        temps               : list of temperatures indexed by experiment (units = Kelvin)
        edots               : edots: list of strain rates indexed by experiment (units = NEED TO ADD)
        consts              : dictionary of constants for PTW model. Use showdef_ModelMaterialStrength(str_func_name) to figure out the needed constants
        strain_histories    : List of strain histories for HB/Quasistatic Experiments
        flow_stress_model   : options provided by getoptions_ModelMaterialStrength()['flow_stress_model']
        melt_model          : options provided by getoptions_ModelMaterialStrength()['melt_model']
        shear_model         : options provided by getoptions_ModelMaterialStrength()['shear_model']
        specific_heat_model : options provided by getoptions_ModelMaterialStrength()['specific_heat_model']
        density_model       : options provided by getoptions_ModelMaterialStrength()['density_model']
        pool                : False if fitting hierarchical model, True if fitting pooled model
        s2                  : method for handling experiment-specific noise s2; options are 'MH' (Metropolis-Hastings Sampling), 'fix' (fixed at s2_est from addVecExperiments call)
        """
        self.meas_strain_histories = strain_histories
        self.meas_strain_max = np.array([v.max() for v in strain_histories])
        self.strain_max = self.meas_strain_max.max()
        self.nhists = sum([len(v) for v in strain_histories])
        self.model = pm_vec.MaterialModel(
            flow_stress_model=eval('pm_vec.' + flow_stress_model), 
            shear_modulus_model=eval('pm_vec.' + shear_model),
            specific_heat_model=eval('pm_vec.' + specific_heat_model),
            melt_model=eval('pm_vec.' + melt_model),
            density_model=eval('pm_vec.' + density_model)
            )
        self.model_info = [flow_stress_model, shear_model, specific_heat_model, melt_model, density_model]
        self.constants = consts
        self.temps = temps
        self.edots = edots
        self.nexp = len(strain_histories)
        self.Nhist = 100
        self.stochastic = False
        self.pool = pool
        self.yobs = None
        self.nd = 0
        self.s2 = s2
        
        #self.meas_error_cor = np.diag(self.basis.shape[0])
        return


    def eval(self, parmat, pool = None, nugget=False): # note: extra parameters ignored
        """ parmat:  dictionary of parameters """
        if (pool is True) or self.pool:  # Pooled Case
            #nrep = parmat['p'].shape[0]  # number of temper temps
            nrep = list(parmat.values())[0].shape[0]
            parmat_big = {key : np.kron(parm, np.ones(self.nexp)) for key, parm in parmat.items()}
        else: # hierarchical case
            nrep = list(parmat.values())[0].shape[0] // self.nexp # number of temper temps
            parmat_big = parmat

        edots = np.kron(np.ones(nrep), self.edots) # 1d vector, nexp * temper_temps
        temps = np.kron(np.ones(nrep), self.temps) # 1d vector, nexp * temper_temps
        strain_maxs = np.kron(np.ones(nrep), self.meas_strain_max) # 1d vector, nexp * temper_temps
        ntot = edots.shape[0]  # nexp * temper_temps
        sim_strain_histories = pm_vec.generate_strain_history_new(strain_maxs, edots, self.Nhist)
        self.model.initialize(parmat_big, self.constants)
        self.model.initialize_state(T = temps, stress = np.zeros(ntot), strain = np.zeros(ntot))
        sim_state_histories = self.model.compute_state_history(sim_strain_histories)
        sim_strains = sim_state_histories[:,1].T  # 2d array: ntot, Nhist
        sim_stresses = sim_state_histories[:,2].T # 2d array: ntot, Nhist

        # Expand/Flatten Simulated strains to single vector--ensure no overlap.
        strain_ends = np.hstack((0., np.cumsum(strain_maxs + epsilon)[:-1])) # 1d vector, ntot
        flattened_sim_strain = np.hstack( # 1d vector: ntot * Nhist
            [x + y for x, y in zip(sim_strains, strain_ends)]
            )
        # Expand/flatten simulated stress to single vector
        flattened_sim_stress = np.hstack(sim_stresses)
        # Expand/flatten measured strain to single vector, for each parameter.  Use same
        #  Computed strain ends to ensure no overlap
        flattened_strain = np.hstack( # cycle will repeat through measured strain histories
            [x + y for x, y in zip(cycle(self.meas_strain_histories), strain_ends)]
            )
        ifunc = interp1d(  # Generate the interpolation function.
            flattened_sim_strain, flattened_sim_stress, kind = 'linear', assume_sorted = True
            )
        ypred = ifunc(flattened_strain).reshape(nrep, -1)  # Interpolate, and output.
        return ypred

def interpolate_experiment(args):
    """ Interpolate and predict at x.  Args is tuple(x_observed, y_observed, x_new) """
    ifunc = interp1d(args[0], args[1], kind = 'cubic')
    return ifunc(args[2])


#######
### getoptions_ModelMaterialStrength: Provides current options for ModelMaterialStrength physical models
def getoptions_ModelMaterialStrength():
    import impala
    import re
    mod_options = dir(impala.physics.physical_models_vec)
    flow_stress_model = list(filter(re.compile(".*Yield_Stress").match, mod_options))
    flow_stress_model = np.append(flow_stress_model,list(filter(re.compile(".*Flow_Stress").match, mod_options)))
    flow_stress_model = list(flow_stress_model)
    melt_model = list(filter(re.compile(".*Melt_Temperature").match, mod_options))
    shear_model = list(filter(re.compile(".*Shear_Modulus").match, mod_options))
    specific_heat_model = list(filter(re.compile(".*Specific_Heat").match, mod_options))
    density_model = list(filter(re.compile(".*Density").match, mod_options))
    return dict({'flow_stress_model': flow_stress_model, 
                'melt_model': melt_model, 
                'shear_model': shear_model, 
                'specific_heat_model': specific_heat_model, 
                'density_model': density_model})

#######
### showdef_ModelMaterialStrength: Shows the definition for a given ModelMaterialStrength function listed in getoptions_ModelMaterialStrength()
def showdef_ModelMaterialStrength(func_name):
    """
    func_name: string listing a function listed in get_ModelMaterialStrength_options(), e.g., showdef_ModelMaterialStrength('Linear_Specific_Heat')
    """
    import impala
    import inspect
    my_func = getattr(impala.physics, func_name) 
    print(inspect.getsource(my_func))


# EOF
