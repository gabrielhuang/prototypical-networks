import numpy as np
import torch
from scipy.optimize import linear_sum_assignment as hungarian


def compute_sinkhorn(m, r=None, c=None, regularization=100., iterations=40):
    '''
    pairwise_distances: (batch, batch')
    r: (batch, dims) distribution (histogram)
    c: (batch', dims) distribution (histogram)
    '''
    # If no distributions are given, consider two uniform histograms
    if r is None:
        r = torch.ones(m.size()[0]).to(m.device) / m.size()[0]
    if c is None:
        c = torch.ones(m.size()[1]).to(m.device) / m.size()[1]

    # Initialize dual variable v (u is implicitly defined in the loop)
    v = torch.ones(m.size()[1]).to(m.device)

    # Exponentiate the pairwise distance matrix
    K = torch.exp(-regularization * m)

    # Main loop
    for i in xrange(iterations):
        # Kdiag(v)_ij = sum_k K_ik diag(v)_kj = K_ij v_j
        # Pij = u_i K_ij v_j
        # sum_j Pij = u_i sum_j K_ij v_j = u_i (Kv)_i = r_i
        # -> u_i = r_i / (Kv)_i
        # K * v[None, :]

        # Match r marginals
        u = r / torch.matmul(K, v)

        # Match c marginals
        v = c / torch.matmul(u, K)

        # print 'P', P
        # print '~r', P.sum(1)
        # print '~c', P.sum(0)
        # print 'u', u
        # print 'v', v
    # Compute optimal plan, cost, return everything
    P = u[:, None] * K * v[None, :]  # transport plan
    dst = (P * m).sum()

    return dst, P, u, v


def log_sum_exp(u, dim):
    # Reduce log sum exp along axis
    u_max, __ = u.max(dim=dim, keepdim=True)
    log_sum_exp_u = torch.log(torch.exp(u - u_max).sum(dim)) + u_max.sum(dim)
    return log_sum_exp_u


def naive_log_sum_exp(u, dim):
    return torch.log(torch.sum(torch.exp(u), dim))


def compute_sinkhorn_stable(m, r=None, c=None, log_v=None, regularization=100., iterations=40):
    # If no distributions are given, consider two uniform histograms
    if r is None:
        r = torch.ones(m.size()[0]).to(m.device) / m.size()[0]
    if c is None:
        c = torch.ones(m.size()[1]).to(m.device) / m.size()[1]
    log_r = torch.log(r)
    log_c = torch.log(c)

    # Initialize dual variable v (u is implicitly defined in the loop)
    if log_v is None:
        log_v = torch.zeros(m.size()[1]).to(m.device)  # ==torch.log(torch.ones(m.size()[1]))

    # Exponentiate the pairwise distance matrix
    log_K = -regularization * m

    # Main loop
    for i in xrange(iterations):
        # Match r marginals
        log_u = log_r - log_sum_exp(log_K + log_v[None, :], dim=1)

        # Match c marginals
        log_v = log_c - log_sum_exp(log_u[:, None] + log_K, dim=0)

    # Compute optimal plan, cost, return everything
    log_P = log_u[:, None] + log_K + log_v[None, :]
    P = torch.exp(log_P)  # transport plan
    dst = (P * m).sum()

    return dst, P, log_P, log_u, log_v


def get_pairwise_distances(m, n):
    assert m.size()[1] == n.size()[1]
    assert len(m.size()) == 2 and len(n.size()) == 2
    distance_matrix = ((m[:, :, None] - n.t()[None, :, :])**2).sum(1)
    return distance_matrix


def cluster_wasserstein_flat(X, n_components, regularization=100., iterations=20, stop_gradient=True, add_noise=0.001):
    '''

    :param X: tensor of shape (n_data, n_dim)
    :param n_components: number of centroids
    :param regularization: 1/regularization in sinkhorn
    :param stop_gradient: whether to cut gradients, if so, centroids are considered to be a
        fixed weighted average of the data. That is the weights (optimal transport plan) are considered not to depend
        on the data.
    :return:
    centroids: tensor of shape (n_components, n_dim)
    P: optimal transport plan
    '''

    assert len(X.size()) == 2, 'Please flatten input to cluster_wasserstein'
    centroids = 0.01 * torch.randn((n_components, X.size()[1])).to(X.device)  # should be fine in most cases
    log_v = None
    for iteration in xrange(iterations):

        distances = get_pairwise_distances(X, centroids)
        # Expectation - Compute Sinkhorn distance
        sinkhorn_iterations = 20 if iteration == 0 else 4
        dst, P, log_P, log_u, log_v = compute_sinkhorn_stable(distances,
                                                       regularization=regularization,
                                                       log_v=log_v,
                                                       iterations=sinkhorn_iterations)
        soft_assignments = P / P.sum(0, keepdim=True)  # P_ij / sum_i P_ij is soft-assignment of cluster j
        # TODO: maybe dividing by constant will simplify graphs?

        if stop_gradient:
            soft_assignments.detach_()  # how bad is that?

        # Minimization
        centroids = torch.matmul(soft_assignments.t(), X)

        if add_noise > 0:
            centroids.add_(add_noise * torch.randn(centroids.size()).to(X.device))

    return centroids, P


def cluster_wasserstein(X, n_components, regularization=100., iterations=20, stop_gradient=True, add_noise=0.001):
    X_flat = X.view((len(X), -1))
    centroids_flat, P = cluster_wasserstein_flat(X_flat, n_components, regularization, iterations, stop_gradient, add_noise)
    size = list(X.size())
    size[0] = n_components
    centroids = centroids_flat.view(size)
    return centroids, P


def compute_hungarian(m):
    assert m.size()[0] == m.size()[1]
    m_numpy = m.cpu().detach().numpy()
    row, col = hungarian(m_numpy)
    matrix = np.zeros(m.size())
    matrix[row, col] = 1. / float(len(m))
    cost = (matrix * m_numpy).sum()
    return cost, torch.tensor(matrix), col


def cluster_kmeans_flat(X, n_components, iterations=20, kmeansplusplus=False, epsilon=1e-6):
    '''

    :param X: tensor of shape (n_data, n_dim)
    :param n_components: number of centroids
    '''

    assert len(X.size()) == 2, 'Please flatten input to cluster_wasserstein'

    if not kmeansplusplus:  # initialize from points of dataset
        indices = np.random.choice(range(len(X)), size=n_components, replace=False)
        centroids = X[indices].clone()
    else:  # follow k-means++ initialization scheme
        initial_idx = np.random.randint(len(X))
        centroids_list = []
        for i in xrange(n_components):
            if i == 0:
                new_idx = np.random.randint(len(X))
            else:
                # Compute distances from each point to closest center
                D = get_pairwise_distances(X, centroids)
                D, __ = D.min(1)
                p = D.detach().cpu().numpy()
                p = p / p.sum()  # needs to normalize in numpy for numerical errors
                new_idx = np.random.choice(range(len(X)), p=p)
            centroids_list.append(X[new_idx].clone())
            centroids = torch.stack(centroids_list)

    assignment_matrix = torch.zeros((len(X), n_components)).to(X.device)
    rows = range(len(X))
    for iteration in xrange(iterations):

        # Get  pairwise distances
        distances = get_pairwise_distances(X, centroids)

        # Expectation - Assign each point to closest centroid
        assignment = distances.argmin(1)
        assignment_matrix.fill_(0)
        assignment_matrix[rows, assignment] = 1.

        # Maximization - Average assigned points
        weights = assignment_matrix / (epsilon + assignment_matrix.sum(0))
        centroids = torch.matmul(weights.t(), X)

    return centroids


def cluster_kmeans(X, n_components, iterations=20, kmeansplusplus=False, epsilon=1e-6):
    X_flat = X.view((len(X), -1))
    centroids_flat = cluster_kmeans_flat(X, n_components, iterations, kmeansplusplus, epsilon)
    size = list(X.size())
    size[0] = n_components
    centroids = centroids_flat.view(size)
    return centroids
