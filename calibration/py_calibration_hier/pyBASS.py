#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Apr  3 14:10:54 2020

@author: dfrancom
"""

import numpy as np
import scipy as sp
from math import pi, sqrt, log, erf, exp, sin
import matplotlib.pyplot as plt
from itertools import combinations, chain
from scipy.special import comb
from datetime import datetime
from collections import namedtuple
import ipdb


def abline(slope, intercept):
    """Plot a line from slope and intercept"""
    axes = plt.gca()
    x_vals = np.array(axes.get_xlim())
    y_vals = intercept + slope * x_vals
    plt.plot(x_vals, y_vals, '--', color='red')

pos = lambda a: (abs(a)+a)/2

def const(signs,knots):
  cc = np.prod(((signs+1)/2 - signs*knots))
  if cc==0:
    return 1
  return cc

## make basis function (from continuous variables)
def makeBasis(signs,vs,knots,xdata):
  cc = const(signs,knots)
  temp1 = pos(signs * (xdata[:,vs]-knots))
  if len(signs) == 1:
      return temp1/cc
  temp2 = np.prod(temp1,axis=1)/cc
  return temp2

def normalize(x, bounds):
    return (x - bounds[:,0]) / (bounds[:,1] - bounds[:,0])

def unnormalize(z, bounds):
    return z * (bounds[:,1] - bounds[:,0]) + bounds[:,0]

def comb_index(n, k):
    count = comb(n, k, exact=True)
    index = np.fromiter(chain.from_iterable(combinations(range(n), k)),
                        int, count=count*k)
    return index.reshape(-1, k)


def dmwnchBass(z_vec, vars_use):
    alpha = z_vec[vars_use-1] / sum(np.delete(z_vec, vars_use))
    j = len(alpha)
    ss = 1 + (-1)**j * 1 / (sum(alpha) + 1)
    for i in range(j-1):
        idx = comb_index(j, i + 1)
        temp = alpha[idx]
        ss = ss + (-1)**(i + 1) * sum(1 / (temp.sum(axis = 1) + 1))
    return ss

Qf = namedtuple('Qf', 'R bhat qf')
def getQf(XtX, Xty):
    try:
        R = sp.linalg.cholesky(XtX,lower=False) # might be a better way to do this with sp.linalg.cho_factor
    except np.linalg.LinAlgError as e:
        return None
    dr = np.diag(R)
    if len(dr) > 1:
        if max(dr[1:]) / min(dr) > 1e3:
            return None
    bhat = sp.linalg.solve_triangular(R, sp.linalg.solve_triangular(R, Xty, trans=1))
    qf = np.dot(bhat, Xty)
    return Qf(R, bhat, qf)


def logProbChangeMod(n_int, vars_use, I_vec, z_vec, p, maxInt):
    if n_int == 1:
        out = (log(I_vec[n_int - 1]) - log(2 * p) # proposal
                        + log(2 * p) + log(maxInt))
    else:
        x = np.zeros(p)
        x[vars_use] = 1
        lprob_vars_noReplace = log(dmwnchBass(z_vec, vars_use))
        out = (log(I_vec[n_int-1]) + lprob_vars_noReplace - n_int * log(2) # proposal
            + n_int * log(2) + log(comb(p,n_int)) + log(maxInt)) # prior
    return out

CandidateBasis = namedtuple('CandidateBasis', 'basis n_int signs vs knots lbmcmp')
def genCandBasis(maxInt, I_vec, z_vec, p, xdata):
    n_int = int(np.random.choice(range(maxInt), p=I_vec) + 1)
    signs = np.random.choice([-1,1], size=n_int, replace=True)
    #knots = np.random.rand(n_int)
    knots = np.zeros(n_int)
    if n_int == 1:
        vs = np.random.choice(p)
        knots = np.random.choice(xdata[:,vs], size=1)
    else:
        vs = np.sort(np.random.choice(p, size=n_int, p=z_vec, replace=False))
        for i in range(n_int):
            knots[i] = np.random.choice(xdata[:,vs[i]], size=1)

    basis = makeBasis(signs, vs, knots, xdata)
    lbmcmp = logProbChangeMod(n_int, vs, I_vec, z_vec, p, maxInt)
    return CandidateBasis(basis, n_int, signs, vs, knots, lbmcmp)

BasisChange = namedtuple('BasisChange', 'basis signs vs knots')
def genBasisChange(knots, signs, vs, tochange_int, xdata):
    knots_cand = knots.copy()
    signs_cand = signs.copy()
    signs_cand[tochange_int] = np.random.choice([-1,1], size=1)
    knots_cand[tochange_int] = np.random.choice(xdata[:,vs[tochange_int]], size=1)#np.random.rand(1)
    basis = makeBasis(signs_cand, vs, knots_cand, xdata)
    return BasisChange(basis, signs_cand, vs, knots_cand)


class BassPrior:
    def __init__(self, maxInt, maxBasis, npart, g1, g2, s2_lower, h1, h2, a_tau, b_tau, w1, w2):
        self.maxInt = maxInt
        self.maxBasis = maxBasis
        self.npart = npart
        self.g1 = g1
        self.g2 = g2
        self.s2_lower = s2_lower
        self.h1 = h1
        self.h2 = h2
        self.a_tau = a_tau
        self.b_tau = b_tau
        self.w1 = w1
        self.w2 = w2
        return


class BassData:
    def __init__(self, xx, y):
        self.xx_orig = xx
        self.y = y
        self.ssy = sum(y*y)
        self.n = len(xx)
        self.p = len(xx[0])
        self.bounds = np.zeros([p, 2])
        for i in range(p):
            self.bounds[i, 0] = np.min(xx[:, i])
            self.bounds[i, 1] = np.max(xx[:, i])
        self.xx = xx#normalize(self.xx_orig, self.bounds)
        return

class BassState:
    def log_post(self): # needs updating
        lp = (
            - (self.s2_rate + self.prior.g2) / self.s2
            - (self.data.n/2 + 1 + (self.nbasis + 1) / 2 + self.prior.g1) * log(self.s2)
            + np.sum(log(abs(np.diag(self.R)))) # .5*determinant of XtX
            + (self.prior.a_tau + (self.nbasis + 1) / 2 - 1) * log(self.tau) - self.prior.a_tau * self.tau
            - (self.nbasis + 1) / 2 * log(2 * pi)
            + (self.prior.h1 + self.nbasis - 1) * log(self.lam) - self.lam * (self.prior.h2 + 1)
            )# curr$nbasis-1 because poisson prior is excluding intercept (for curr$nbasis instead of curr$nbasis+1)
    #-lfactorial(curr$nbasis) # added, but maybe cancels with prior
        self.lp = lp
        return

    def update(self):
        move_type = np.random.choice([1,2,3])

        if self.nbasis == 0:
            move_type = 1

        if self.nbasis == self.prior.maxBasis:
            move_type = np.random.choice(np.array([2,3]))

        if move_type == 1:
            ## BIRTH step

            cand = genCandBasis(self.prior.maxInt, self.I_vec, self.z_vec, self.data.p, self.data.xx)

            if (cand.basis>0).sum() < self.prior.npart: # if proposed basis function has too few non-zero entries, dont change the state
                return

            ata = np.dot(cand.basis,cand.basis)
            Xta = np.dot(self.basis.T,cand.basis)
            aty = np.dot(cand.basis,self.data.y)

            self.Xty[self.nc] = aty
            self.XtX[0:self.nc,self.nc] = Xta
            self.XtX[self.nc,0:(self.nc)] = Xta
            self.XtX[self.nc,self.nc] = ata

            qf_cand = getQf(self.XtX[0:(self.nc+1),0:(self.nc+1)],self.Xty[0:(self.nc+1)])

            fullRank = qf_cand != None
            if not fullRank:
              return

            alpha = .5/self.s2 * (qf_cand.qf - self.qf) / (1 + self.tau) + log(self.lam) - log(self.nc) + log(1/3) - log(1/3) - cand.lbmcmp + .5*log(self.tau) - .5*log(1+self.tau)

            if log(np.random.rand()) < alpha:
                self.cmod = True
                # note, XtX and Xty are already updated
                self.nbasis = self.nbasis + 1
                self.nc = self.nbasis + 1
                self.qf = qf_cand.qf
                self.bhat = qf_cand.bhat
                self.R = qf_cand.R
                self.R_inv_t = sp.linalg.solve_triangular(self.R,np.identity(self.nc)) # check this
                self.count[0] = self.count[0]+1
                self.n_int[self.nbasis-1] = cand.n_int
                self.knots[self.nbasis-1, 0:(cand.n_int)] = cand.knots
                self.signs[self.nbasis-1, 0:(cand.n_int)] = cand.signs
                self.vs[self.nbasis-1, 0:(cand.n_int)] = cand.vs

                self.I_star[cand.n_int-1] = self.I_star[cand.n_int-1]+1
                self.I_vec = self.I_star/sum(self.I_star)
                self.z_star[cand.vs] = self.z_star[cand.vs]+1
                self.z_vec = self.z_star/sum(self.z_star)

                self.basis = np.append(self.basis,cand.basis.reshape(self.data.n,1),axis=1)


        elif move_type == 2:
            ## DEATH step

            tokill_ind = np.random.choice(self.nbasis)
            ind = list(range(self.nc))
            del ind[tokill_ind+1]

            qf_cand = getQf(self.XtX[np.ix_(ind,ind)],self.Xty[ind])

            fullRank = qf_cand != None
            if not fullRank:
              return

            I_star = self.I_star.copy()
            I_star[self.n_int[tokill_ind]-1] = I_star[self.n_int[tokill_ind]-1]-1
            I_vec = I_star/sum(I_star)
            z_star = self.z_star.copy()
            z_star[self.vs[tokill_ind,0:self.n_int[tokill_ind]]] = z_star[self.vs[tokill_ind,0:self.n_int[tokill_ind]]] - 1

            z_vec = z_star/sum(z_star)

            lbmcmp = logProbChangeMod(self.n_int[tokill_ind],self.vs[tokill_ind,0:self.n_int[tokill_ind]],I_vec,z_vec,self.data.p,self.prior.maxInt)

            alpha = .5/self.s2 * (qf_cand.qf - self.qf) / (1 + self.tau) - log(self.lam) + log(self.nbasis) + log(1/3) - log(1/3) + lbmcmp - .5*log(self.tau) + .5*log(1+self.tau)

            if log(np.random.rand()) < alpha:
                self.cmod = True
                self.nbasis = self.nbasis - 1
                self.nc = self.nbasis + 1
                self.qf = qf_cand.qf
                self.bhat = qf_cand.bhat
                self.R = qf_cand.R
                self.R_inv_t = sp.linalg.solve_triangular(self.R,np.identity(self.nc)) # check this
                self.count[1] = self.count[1]+1

                self.Xty[0:self.nc] = self.Xty[ind]
                self.XtX[0:self.nc,0:self.nc] = self.XtX[np.ix_(ind,ind)]

                temp = self.n_int[0:(self.nbasis+1)]
                temp = np.delete(temp, tokill_ind)
                self.n_int = self.n_int*0
                self.n_int[0:(self.nbasis)] = temp[:]

                temp = self.knots[0:(self.nbasis+1),:]
                temp = np.delete(temp, tokill_ind,0)
                self.knots = self.knots*0
                self.knots[0:(self.nbasis),:] = temp[:]

                temp = self.signs[0:(self.nbasis+1),:]
                temp = np.delete(temp, tokill_ind,0)
                self.signs = self.signs*0
                self.signs[0:(self.nbasis),:] = temp[:]

                temp = self.vs[0:(self.nbasis+1),:]
                temp = np.delete(temp, tokill_ind,0)
                self.vs = self.vs*0
                self.vs[0:(self.nbasis),:] = temp[:]

                self.I_star = I_star[:]
                self.I_vec = I_vec[:]
                self.z_star = z_star[:]
                self.z_vec = z_vec[:]

                self.basis = np.delete(self.basis,tokill_ind+1,1)

        else:
            ## CHANGE step

            tochange_basis = np.random.choice(self.nbasis)
            tochange_int = np.random.choice(self.n_int[tochange_basis])

            cand = genBasisChange(self.knots[tochange_basis, 0:self.n_int[tochange_basis]], self.signs[tochange_basis, 0:self.n_int[tochange_basis]], self.vs[tochange_basis, 0:self.n_int[tochange_basis]], tochange_int, self.data.xx)

            if (cand.basis>0).sum() < self.prior.npart: # if proposed basis function has too few non-zero entries, dont change the state
                return

            ata = np.dot(cand.basis.T,cand.basis)
            Xta = np.dot(self.basis.T,cand.basis).reshape(self.nc)
            aty = np.dot(cand.basis.T,self.data.y)

            ind = list(range(self.nc))
            XtX_cand = self.XtX[np.ix_(ind,ind)].copy()
            XtX_cand[tochange_basis+1,:] = Xta
            XtX_cand[:,tochange_basis+1] = Xta
            XtX_cand[tochange_basis+1,tochange_basis+1] = ata

            Xty_cand = self.Xty[0:self.nc].copy()
            Xty_cand[tochange_basis+1] = aty

            qf_cand = getQf(XtX_cand,Xty_cand)

            fullRank = qf_cand != None
            if not fullRank:
              return

            alpha = .5/self.s2 * (qf_cand.qf - self.qf) / (1 + self.tau)

            if log(np.random.rand()) < alpha:
                self.cmod = True
                self.qf = qf_cand.qf
                self.bhat = qf_cand.bhat
                self.R = qf_cand.R
                self.R_inv_t = sp.linalg.solve_triangular(self.R,np.identity(self.nc)) # check this
                self.count[2] = self.count[2]+1

                self.Xty[0:self.nc] = Xty_cand
                self.XtX[0:self.nc,0:self.nc] = XtX_cand

                self.knots[tochange_basis, 0:self.n_int[tochange_basis]] = cand.knots
                self.signs[tochange_basis, 0:self.n_int[tochange_basis]] = cand.signs

                self.basis[:,tochange_basis+1] = cand.basis.reshape(self.data.n)

        a_s2 = self.prior.g1 + self.data.n/2
        b_s2 = self.prior.g2 + .5*(self.data.ssy - np.dot(self.bhat.T,self.Xty[0:self.nc])/(1+self.tau))
        self.s2 = 1/np.random.gamma(a_s2,1/b_s2,size=1)

        self.beta = self.bhat/(1+self.tau) + np.dot(self.R_inv_t,np.random.normal(size=self.nc)) * sqrt(self.s2/(1+self.tau))


        a_lam = self.prior.h1 + self.nbasis
        b_lam = self.prior.h2 + 1
        self.lam = np.random.gamma(a_lam,1/b_lam,size=1)

        temp = np.dot(self.R,self.beta)
        qf2 = np.dot(temp,temp)
        a_tau = self.prior.a_tau + (self.nbasis+1)/2
        b_tau = self.prior.b_tau + .5*qf2/self.s2
        self.tau = np.random.gamma(a_tau,1/b_tau,size=1)



    def __init__(self, data, prior):
        self.data = data
        self.prior = prior
        self.s2 = 1.
        self.nbasis = 0
        self.tau = 1.
        self.s2_rate = 1.
        self.R = 1
        self.lam = 1
        self.I_star = np.ones(prior.maxInt) * prior.w1
        self.I_vec = self.I_star/np.sum(self.I_star)
        self.z_star = np.ones(data.p) * prior.w2
        self.z_vec = self.z_star/np.sum(self.z_star)
        self.basis = np.ones([data.n, 1])
        self.nc = 1
        self.knots = np.zeros([prior.maxBasis, prior.maxInt])
        self.signs = np.zeros([prior.maxBasis, prior.maxInt], dtype = int) # could do "bool_", but would have to transform 0 to -1
        self.vs = np.zeros([prior.maxBasis, prior.maxInt], dtype = int)
        self.n_int = np.zeros([prior.maxBasis], dtype = int)
        self.Xty = np.zeros(prior.maxBasis + 2)
        self.Xty[0] = np.sum(data.y)
        self.XtX = np.zeros([prior.maxBasis + 2, prior.maxBasis + 2])
        self.XtX[0, 0] = data.n
        self.R = np.array([[sqrt(data.n)]])#np.linalg.cholesky(self.XtX[0, 0])
        self.R_inv_t = np.array([[1/sqrt(data.n)]])
        self.bhat = np.mean(data.y)
        self.qf = pow(sqrt(data.n) * np.mean(data.y), 2)
        self.count = np.zeros(3)
        self.cmod = False
        return



class BassModel:
    def __init__(self, data, prior, nstore):
        self.data = data
        self.prior = prior
        self.state = BassState(self.data, self.prior)
        self.nstore = nstore
        s2 =  np.zeros(nstore)
        lam =  np.zeros(nstore)
        tau = np.zeros(nstore)
        nbasis = np.zeros(nstore,dtype=int)
        nbasis_models = np.zeros(nstore,dtype=int)
        n_int = np.zeros([nstore,self.prior.maxBasis],dtype=int)
        signs = np.zeros([nstore,self.prior.maxBasis,self.prior.maxInt],dtype=int)
        vs = np.zeros([nstore,self.prior.maxBasis,self.prior.maxInt],dtype=int)
        knots = np.zeros([nstore,self.prior.maxBasis,self.prior.maxInt])
        beta = np.zeros([nstore,self.prior.maxBasis+1])
        out = namedtuple('out', 's2 lam tau nbasis nbasis_models n_int signs vs knots beta')
        self.samples = out(s2, lam, tau, nbasis, nbasis_models, n_int, signs, vs, knots, beta)
        self.k = 0
        self.k_mod = -1
        self.model_lookup = np.zeros(nstore,dtype=int)
        return

    def writeState(self): # take relevant parts of state and write to storage (only manipulates storage vectors created in init)
        #ipdb.set_trace()
        self.samples.s2[self.k] = self.state.s2
        self.samples.lam[self.k] = self.state.lam
        self.samples.tau[self.k] = self.state.tau
        self.samples.beta[self.k,0:(self.state.nbasis+1)] = self.state.beta
        self.samples.nbasis[self.k] = self.state.nbasis


        if self.state.cmod:
            self.k_mod = self.k_mod + 1
            self.samples.nbasis_models[self.k_mod] = self.state.nbasis
            self.samples.n_int[self.k_mod,0:self.state.nbasis] = self.state.n_int[0:self.state.nbasis]
            self.samples.signs[self.k_mod,0:self.state.nbasis,:] = self.state.signs[0:self.state.nbasis,:]
            self.samples.vs[self.k_mod,0:self.state.nbasis,:] = self.state.vs[0:self.state.nbasis,:]
            self.samples.knots[self.k_mod,0:self.state.nbasis,:] = self.state.knots[0:self.state.nbasis,:]
            self.state.cmod = False

        self.model_lookup[self.k] = self.k_mod
        self.k = self.k + 1


    def plot(self):
        fig = plt.figure()

        ax = fig.add_subplot(2,2,1)
        plt.plot(self.samples.nbasis)
        plt.ylabel("number of basis functions")
        plt.xlabel("MCMC iteration (post-burn)")

        ax = fig.add_subplot(2,2,2)
        plt.plot(self.samples.s2)
        plt.ylabel("error variance")
        plt.xlabel("MCMC iteration (post-burn)")

        ax = fig.add_subplot(2,2,3)
        yhat = self.predict(self.data.xx).mean(axis=0) # posterior predictive mean
        plt.scatter(y, yhat)
        abline(1,0)
        plt.xlabel("observed")
        plt.ylabel("posterior prediction")

        ax = fig.add_subplot(2,2,4)
        plt.hist(self.data.y-yhat,color = "skyblue", ec="white",density=True)
        axes = plt.gca()
        x=np.linspace(axes.get_xlim()[0],axes.get_xlim()[1],100)
        plt.plot(x, sp.stats.norm.pdf(x,scale=sqrt(mod.samples.s2.mean())),color='red')
        plt.xlabel("residuals")
        plt.ylabel("density")

        fig.tight_layout()

        plt.show()


    def makeBasisMatrix(self, model_ind, X): # make basis matrix for model
        #ipdb.set_trace()
        nb = self.samples.nbasis_models[model_ind]
        n = len(X)
        mat = np.zeros([n,nb+1])
        mat[:,0] = 1
        for m in range(nb):
            ind = list(range(self.samples.n_int[model_ind,m]))
            mat[:,m+1] = makeBasis(self.samples.signs[model_ind,m,ind],self.samples.vs[model_ind,m,ind],self.samples.knots[model_ind,m,ind],X).reshape(n)
        return mat

    def predict(self, X, mcmc_use = None):
        # needs to handle standardization...

        if mcmc_use == None:
            mcmc_use = np.array(range(self.nstore))
        out = np.zeros([len(mcmc_use),len(X)])
        models = self.model_lookup[mcmc_use]
        umodels = set(models)
        for j in umodels:
            #ipdb.set_trace()
            mcmc_use_j = mcmc_use[np.ix_(models == j)]
            out[mcmc_use_j,:] = np.dot(self.samples.beta[mcmc_use_j,0:(self.samples.nbasis_models[j]+1)],self.makeBasisMatrix(j,X).T)
        return out




def bass(xx, y, nmcmc = 10000, nburn = 9000, thin = 1, w1 = 5, w2 = 5, maxInt = 3, maxBasis = 1000, npart = None, g1 = 0, g2 = 0, s2_lower = 0, h1 = 10, h2 = 10, a_tau = 0.5, b_tau = None):
    if b_tau == None:
        b_tau = len(y)/2
    if npart == None:
        npart = min(20,.1*len(y))
    bd = BassData(xx, y)
    bp = BassPrior(maxInt, maxBasis, npart, g1, g2, s2_lower, h1, h2, a_tau, b_tau, w1, w2)
    nstore = int((nmcmc - nburn) / thin)
    bm = BassModel(bd, bp, nstore) # if we add tempering, bm should have as many states as temperatures
    for i in range(nmcmc): # rjmcmc loop
        bm.state.update()
        if i > (nburn-1) and ((i - nburn + 1) % thin) == 0:
            bm.writeState()
        if i % 1000==0:
            print(str(datetime.now()) + ', nbasis: ' + str(bm.state.nbasis))
    #del bm.writeState # the user should have access to this
    return bm







######################################################
## test it out


def f(x):
  out = 10. * np.sin(pi * x[:,0] * x[:,1]) + 20. * (x[:,2] - .5)**2 + 10 * x[:,3] + 5. * x[:,4]
  return out

n = 500
p = 10
x = np.random.rand(n,p)
xx = np.random.rand(1000,p)
y = f(x) + np.random.normal(size=n)

mod = bass(x,y,nmcmc=10000,nburn=9000)

mod.plot()

print(np.var(mod.predict(xx).mean(axis=0)-f(xx)))

#from pathos.multiprocessing import ProcessingPool as Pool
#import dill

#dill.settings['recurse'] = True

#pool = Pool()
#res_list = list(pool.map(calib_use_list, use, itertools.repeat(use_list, len(use)),itertools.repeat(lookup, len(use))))
#pool.close()
#pool.join()



profiler.print_stats()
profiler.dump_stats("/Users/dfrancom/Desktop/profiler_stats.txt")

#import cProfile
#cProfile.run('bass(x,y)')


# TODO:
    # PCA space function
    # explore whether you need discrete knot prior or not
    # handle standardization
