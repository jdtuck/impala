"""
MPI enabled Parallel Tempering
"""
import pt
MPI_MESSAGE_SIZE = 2**16

class BreakException(Exception):
    pass

class PTSlave(pt.PTSlave):
    def watch(self):
        try:
            recv = self.comm.irecv(MPI_MESSAGE_SIZE, source = 0)
            parcel = recv.wait()
            self.dispatch[parcel[0]](**parcel[1])
        except BreakException:
            pass
        return

    def try_swap_state_sup(self, inf_rank):
        state1 = self.get_state()
        sent = self.comm.isend(state1, dest = inf_rank)
        recv = self.comm.irecv(MPI_MESSAGE_SIZE, source =  inf_rank)
        lp11 = self.log_posterior_state(state1)
        state2 = recv.wait()
        lp12 = self.log_posterior_state(state2)
        recv = self.comm.irecv(source = inf_rank)
        lp21, lp22 = recv.wait()
        log_alpha = lp12 + lp21 - lp11 - lp22
        if log(uniform()) < log_alpha:
            self.comm.send('swap_yes', dest = inf_rank)
            self.set_state(state2)
            self.comm.send(True, dest = 0)
        else:
            self.comm.send('swap_no', dest = inf_rank)
            self.comm.send(False, dest = 0)
        return

    def try_swap_state_inf(self, sup_rank):
        state2 = self.get_state(sup_rank)
        sent = self.comm.isend(state2, dest = sup_rank)
        recv = self.comm.irecv(MPI_MESSAGE_SIZE, source = sup_rank)
        lp22 = self.log_posterior_state(state2)
        state1 = recv.wait()
        lp21 = self.log_posterior_state(state1)
        sent = self.comm.isend((lp21, lp22), dest = sup_rank)
        recv = self.comm.irecv(source = sup_rank)
        parcel = recv.wait()
        if parcel == 'swap_yes':
            self.set_state(state1)
        elif parcel == 'swap_no':
            pass
        else:
            raise ValueError('Acceptable: swap_yes, swap_no; Observed {}'.format(parcel))
        return

    def initialize_chain(self, **kwargs):
        self.chain = self.statmodel(**kwargs)
        self.comm.send(True, dest = 0)
        return

    def complete(self):
        raise BreakException('Done')

    def pairwise_parameter_plot(self, path):
        super().pairwise_parameter_plot(path)
        self.comm.send(True, dest = 0)
        return

    def get_accept_probability(self, nburn):
        self.comm.send(self.chain.get_accept_probability(nburn), dest = 0)
        return

    def write_to_disk(self, path, nburn, thin):
        self.chain.write_to_disk(path, nburn, thin)
        self.comm.send(True, dest = 0)
        return

    def build_dispatch(self):
        self.dispatch = {
            'pairwise_plot'      : self.pairwise_parameter_plot,
            'init_chain'         : self.initialize_chain,
            'set_temperature'    : self.set_temperature,
            'init_sampler'       : self.initialize_sampler,
            'sample_n'           : self.sample_n,
            'get_state'          : self.get_state,
            'set_state'          : self.set_state,
            'get_accept_prob'    : self.get_accept_probability,
            'write_to_disk'      : self.write_to_disk,
            'complete'           : self.complete,
            'try_swap_state_inf' : self.try_swap_state_inf,
            'try_swap_state_sup' : self.try_swap_state_sup,
            }
        return

    def __init__(self, comm, statmodel):
        self.comm      = comm
        self.statmodel = statmodel
        self.build_dispatch()
        pass

class PTMaster(pt.PTMaster):
    def try_swap_states(self, sup_rank, inf_rank):
        sent1 = self.comm.isend(('try_swap_state_sup', {'inf_rank' : inf_rank}), dest = sup_rank)
        sent2 = self.comm.isend(('try_swap_state_inf', {'sup_rank' : sup_rank}), dest = inf_rank)
        return self.comm.irecv(source = sup_rank)

    def temper_chains(self):
        ranks = self.chain_ranks.copy()
        shuffle(ranks)
        swaps = []
        for rank1, rank2 in zip(ranks[::2], ranks[1::2]):
            swaps.append((rank1, rank2, self.try_swap_states(rank_1, rank_2)))
        _swaps = [(rank1, rank2, swapped.wait()) for rank1, rank2, swapped in swaps]
        self.swaps.append(_swaps)
        return

    def sample_n(self, n):
        sent = [self.comm.isend(('sample_n', {'n' : n}), dest = rank) for rank in self.chain_ranks]
        recv = [self.comm.irecv(source = rank) for rank in self.chain_ranks]
        assert all([r.wait() for r in recv])
        return

    def set_temperature_ladder(self, temperature_ladder):
        assert len(temperature_ladder) == len(self.chain_ranks)
        sent = [
            self.comm.isend(('set_temperature', {'temp' : temp}), dest = rank)
            for temp, rank in zip(temperature_ladder, self.chain_ranks)
            ]
        recv = [self.comm.irecv(source = rank) for rank in self.chain_ranks]
        assert all([r.wait() for r in recv])
        return

    def initialize_sampler(self, ns):
        sent = [
            self.comm.isend(('init_sampler', {'ns' : ns}), dest = rank)
            for rank in self.chain_ranks
            ]
        recv = [self.comm.irecv(source = rank) for rank in self.chain_ranks]
        assert all([r.wait() for r in recv])
        return

    def sample(self, ns, k = 5):
        self.initialize_sampler(ns)

        sampled = 0
        print('\rSampling {:.1%} Complete'.format(sampled / ns), end = '')
        for _ in range(ns // k):
            self.sample_n(k)
            self.temper_chains()
            sampled += k
            print('\rSampling {:.1%} Complete'.format(sampled / ns), end = '')

        self.sample_k(ns % k)
        sampled += (ns % k)
        print('\rSampling {:.1%} Complete'.format(sampled / ns))
        return

    def get_swap_probability(self):
        try:
            k = len(self.chain_ranks)
        except NameError:
            k = len(self.chain)
        swap_y = np.zeros((k,k))
        swap_n = np.zeros((k,k))
        for swap_generation in self.swaps:
            for chain_a, chain_b, swapped in swap_generation:
                swap_y[chain_a, chain_b] += swapped
                swap_n[chain_a, chain_b] += 1 - swapped

        swap_y = swap_y + swap_y.T
        swap_n = swap_n + swap_n.T
        return swap_y / (swap_y + swap_n)

    def pairwise_parameter_plot(self, path):
        sent = self.comm.isend(('pairwise_plot', {'path' : path}), dest = 1)
        recv = self.comm.irecv(source = 1)
        parcel = recv.wait()
        assert parcel
        return

    def get_accept_probability(self, nburn):
        sent = [
            self.comm.isend(('get_accept_prob', {'nburn' : nburn}), dest = rank)
            for rank in self.chain_ranks
            ]
        recv = [self.comm.irecv(source = rank) for rank in self.chain_ranks]
        return np.array([r.wait() for r in recv])

    def initialize_chains(self, temperature_ladder, kwargs):
        self.chain_ranks = list(range(1, self.size))
        sent = [
            self.comm.isend(('init_chain', {'temperature' : temp, **kwargs}), dest = rank)
            for temp,  rank in zip(temperature_ladder, self.chain_ranks)
            ]
        recv = [self.comm.irecv(source = rank) for rank in self.chain_ranks]
        assert all([r.wait() for r in recv])
        return

    def write_to_disk(self, path, nburn, thin):
        sent = self.comm.isend(
            ('write_to_disk', {'path' : path, 'nburn' : nburn, 'thin' : thin}),
            dest = 1,
            )
        recv = self.comm.irecv(source = 1)
        parcel = recv.wait()
        assert parcel
        return

    def __init__(self, comm, temperature_ladder, **kwargs):
        self.comm = comm
        self.size = self.comm.Get_size()
        self.initialize_chains(temperature_ladder, kwargs)
        return

# EOF
