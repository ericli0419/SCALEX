#!/usr/bin/env python
"""
# Author: Xiong Lei
# Created Time : Wed 26 Dec 2018 03:46:19 PM CST
# File Name: data.py
# Description:
"""

import os
import numpy as np
import pandas as pd
import scipy
from scipy.sparse import issparse

import torch
from torch.utils.data import Dataset
from torch.utils.data.sampler import Sampler
from torch.utils.data import DataLoader

from anndata import AnnData
import scanpy as sc
from sklearn.preprocessing import maxabs_scale, MaxAbsScaler

from glob import glob

np.warnings.filterwarnings('ignore')
DATA_PATH = os.path.expanduser("~")+'/.scalex/'
CHUNK_SIZE = 20000


def read_mtx(path):
    """\
    Read mtx format data folder including: 
    
        * matrix file: e.g. count.mtx or matrix.mtx or their gz format
        * barcode file: e.g. barcode.txt
        * feature file: e.g. feature.txt
        
    Parameters
    ----------
    path
        the path store the mtx files  
        
    Return
    ------
    AnnData
    """
    for filename in glob(path+'/*'):
        if ('count' in filename or 'matrix' in filename or 'data' in filename) and ('mtx' in filename):
            adata = sc.read_mtx(filename).T
    for filename in glob(path+'/*'):
        if 'barcode' in filename:
            barcode = pd.read_csv(filename, sep='\t', header=None).iloc[:, -1].values
            adata.obs = pd.DataFrame(index=barcode)
        if 'gene' in filename or 'peaks' in filename:
            gene = pd.read_csv(filename, sep='\t', header=None).iloc[:, -1].values
            adata.var = pd.DataFrame(index=gene)
        elif 'feature' in filename:
            gene = pd.read_csv(filename, sep='\t', header=None).iloc[:, 1].values
            adata.var = pd.DataFrame(index=gene)
    return adata


def load_file(path):  
    """
    Load single cell dataset from file
    
    Parameters
    ----------
    path
        the path store the file
        
    Return
    ------
    AnnData
    """
    if os.path.exists(DATA_PATH+path+'.h5ad'):
        adata = sc.read_h5ad(DATA_PATH+path+'.h5ad')
    elif os.path.isdir(path): # mtx format
        adata = read_mtx(path)
    elif os.path.isfile(path):
        if path.endswith(('.csv', '.csv.gz')):
            adata = sc.read_csv(path).T
        elif path.endswith(('.txt', '.txt.gz', '.tsv', '.tsv.gz')):
            df = pd.read_csv(path, sep='\t', index_col=0).T
            adata = AnnData(df.values, dict(obs_names=df.index.values), dict(var_names=df.columns.values))
        elif path.endswith('.h5ad'):
            adata = sc.read_h5ad(path)
    else:
        raise ValueError("File {} not exists".format(path))
        
    if not issparse(adata.X):
        adata.X = scipy.sparse.csr_matrix(adata.X)
    adata.var_names_make_unique()
    return adata


def load_files(root):
    """
    Load single cell dataset from files
    
    Parameters
    ----------
    root
        the root store the single-cell data files, each file represent one dataset
        
    Return
    ------
    AnnData
    """
    if root.split('/')[-1] == '*':
        adata = []
        for root in sorted(glob(root)):
            adata.append(load_file(root))
        return AnnData.concatenate(*adata, batch_key='sub_batch', index_unique=None)
    else:
        return load_file(root)
    
    
def concat_data(
        data_list, 
        batch_categories=None, 
        join='inner',             
        batch_key='batch', 
        index_unique=None, 
        save=None
    ):
    """
    Concatenate multiple datasets along the observations axis with name ``batch_key``.
    
    Parameters
    ----------
    data_list
        A path list of AnnData matrices to concatenate with. Each matrix is referred to as a “batch”.
    batch_categories
        Categories for the batch annotation. By default, use increasing numbers.
    join
        Use intersection ('inner') or union ('outer') of variables of different batches. Default: 'inner'.
    batch_key
        Add the batch annotation to obs using this key. Default: 'batch'.
    index_unique
        Make the index unique by joining the existing index names with the batch category, using index_unique='-', for instance. Provide None to keep existing indices.
    save
        Path to save the new merged AnnData. Default: None.
        
    Returns
    -------
    New merged AnnData.
    """
    if len(data_list) == 1:
        return load_files(data_list[0])
    elif isinstance(data_list, str):
        return load_files(data_list)
    adata_list = []
    for root in data_list:
        adata = load_files(root)
        adata_list.append(adata)
        
    if batch_categories is None:
        batch_categories = list(map(str, range(len(adata_list))))
    else:
        assert len(adata_list) == len(batch_categories)
    [print(b, adata.shape) for adata,b in zip(adata_list, batch_categories)]
    concat = AnnData.concatenate(*adata_list, join=join, batch_key=batch_key,
                                batch_categories=batch_categories, index_unique=index_unique)  
    if save:
        concat.write(save, compression='gzip')
    return concat
        
    
def preprocessing_rna(
        adata: AnnData, 
        min_features: int = 600, 
        min_cells: int = 3, 
        target_sum: int = 10000, 
        n_top_features = 2000, # or gene list
        chunk_size: int = CHUNK_SIZE,
        log=None
    ):
    """
    Preprocessing single-cell RNA-seq data
    
    Parameters
    ----------
    adata
        An AnnData matrice of shape n_obs × n_vars. Rows correspond to cells and columns to genes.
    min_features
        Filtered out cells that are detected in less than n genes. Default: 600.
    min_cells
        Filtered out genes that are detected in less than n cells. Default: 3.
    target_sum
        After normalization, each cell has a total count equal to target_sum. If None, total count of each cell equal to the median of total counts for cells before normalization.
    n_top_features
        Number of highly-variable genes to keep. Default: 2000.
    chunk_size
        Number of samples from the same batch to transform. Default: 20000.
    log
        If log, record each operation in the log file. Default: None.
        
    Return
    -------
    The AnnData object after preprocessing.
    """
    if min_features is None: min_features = 600
    if n_top_features is None: n_top_features = 2000
    
    if log: log.info('Preprocessing')
    if not issparse(adata.X):
        adata.X = scipy.sparse.csr_matrix(adata.X)
    
    adata = adata[:, [gene for gene in adata.var_names 
                  if not str(gene).startswith(tuple(['ERCC', 'MT-', 'mt-']))]]
    
    if log: log.info('Filtering cells')
    sc.pp.filter_cells(adata, min_genes=min_features)
    
    if log: log.info('Filtering features')
    sc.pp.filter_genes(adata, min_cells=min_cells)

    if log: log.info('Normalizing total per cell')
    sc.pp.normalize_total(adata, target_sum=target_sum)
    
    if log: log.info('Log1p transforming')
    sc.pp.log1p(adata)
    
    adata.raw = adata
    if log: log.info('Finding variable features')
    if type(n_top_features) == int and n_top_features>0:
        sc.pp.highly_variable_genes(adata, n_top_genes=n_top_features, batch_key='batch', inplace=False, subset=True)
    elif type(n_top_features) != int:
        adata = reindex(adata, n_top_features)
        
    if log: log.info('Batch specific maxabs scaling')
    adata = batch_scale(adata, chunk_size=chunk_size)
    if log: log.info('Processed dataset shape: {}'.format(adata.shape))
    return adata


def preprocessing_atac(
        adata: AnnData, 
        min_features: int = 100, 
        min_cells: int = 3, 
        target_sum=None, 
        n_top_features = 30000, # or gene list
        chunk_size: int = CHUNK_SIZE,
        log=None
    ):
    """
    Preprocessing single-cell ATAC-seq
    
    Parameters
    ----------
    adata
        An AnnData matrice of shape n_obs × n_vars. Rows correspond to cells and columns to genes.
    min_features
        Filtered out cells that are detected in less than n genes. Default: 100.
    min_cells
        Filtered out genes that are detected in less than n cells. Default: 3.
    target_sum
        After normalization, each cell has a total count equal to target_sum. If None, total count of each cell equal to the median of total counts for cells before normalization. Default: None.
    n_top_features
        Number of highly-variable features to keep. Default: 30000.
    chunk_size
        Number of samples from the same batch to transform. Default: 20000.
    log
        If log, record each operation in the log file. Default: None.
        
    Return
    -------
    The AnnData object after preprocessing.
    """
    import episcanpy as epi
    
    if min_features is None: min_features = 100
    if n_top_features is None: n_top_features = 30000
    
    if log: log.info('Preprocessing')
    if not issparse(adata.X):
        adata.X = scipy.sparse.csr_matrix(adata.X)
    
    adata.X[adata.X>1] = 1
    
    if log: log.info('Filtering cells')
    sc.pp.filter_cells(adata, min_genes=min_features)
    
    if log: log.info('Filtering features')
    sc.pp.filter_genes(adata, min_cells=min_cells)
    
#     adata.raw = adata
    if log: log.info('Finding variable features')
    if type(n_top_features) == int and n_top_features>0:
        epi.pp.select_var_feature(adata, nb_features=n_top_features, show=False, copy=False)
    elif type(n_top_features) != int:
        adata = reindex(adata, n_top_features)

    
    if log: log.info('Normalizing total per cell')
    sc.pp.normalize_total(adata, target_sum=target_sum)

        
    if log: log.info('Batch specific maxabs scaling')
    adata = batch_scale(adata, chunk_size=chunk_size)
    if log: log.info('Processed dataset shape: {}'.format(adata.shape))
    return adata




def preprocessing(
        adata: AnnData, 
        profile: str='RNA',
        min_features: int = 600, 
        min_cells: int = 3, 
        target_sum: int = 10000, 
        n_top_features = 2000, # or gene list
        chunk_size: int = CHUNK_SIZE,
        log=None,
    ):
    """
    Preprocessing single-cell data
    
    Parameters
    ----------
    adata
        An AnnData matrice of shape n_obs × n_vars. Rows correspond to cells and columns to genes.
    profile
        Specify the single-cell profile type, RNA or ATAC, Default: RNA.
    min_features
        Filtered out cells that are detected in less than n genes. Default: 100.
    min_cells
        Filtered out genes that are detected in less than n cells. Default: 3.
    target_sum
        After normalization, each cell has a total count equal to target_sum. If None, total count of each cell equal to the median of total counts for cells before normalization.
    n_top_features
        Number of highly-variable genes to keep. Default: 2000.
    chunk_size
        Number of samples from the same batch to transform. Default: 20000.
    log
        If log, record each operation in the log file. Default: None.
        
    Return
    -------
    The AnnData object after preprocessing.
    
    """
    if profile == 'RNA':
        return preprocessing_rna(
                   adata, 
                   min_features=min_features, 
                   min_cells=min_cells, 
                   n_top_features=n_top_features, 
                   chunk_size=chunk_size, 
                   log=log
               )
    elif profile == 'ATAC':
        return preprocessing_atac(
                   adata, 
                   min_features=min_features, 
                   min_cells=min_cells, 
                   n_top_features=n_top_features, 
                   chunk_size=chunk_size, 
                   log=log
               )

        
def batch_scale(adata, chunk_size=CHUNK_SIZE):
    """
    Batch-specific scale data
    
    Parameters
    ----------
    adata
        AnnData
    chunk_size
        chunk large data into small chunks
    
    Return
    ------
    AnnData
    """
    for b in adata.obs['batch'].unique():
        idx = np.where(adata.obs['batch']==b)[0]
        scaler = MaxAbsScaler(copy=False).fit(adata.X[idx])
        for i in range(len(idx)//chunk_size+1):
            adata.X[idx[i*chunk_size:(i+1)*chunk_size]] = scaler.transform(adata.X[idx[i*chunk_size:(i+1)*chunk_size]])

    return adata
        

def reindex(adata, genes, chunk_size=CHUNK_SIZE):
    """
    Reindex AnnData with gene list
    
    Parameters
    ----------
    adata
        AnnData
    genes
        gene list for indexing
    chunk_size
        chunk large data into small chunks
        
    Return
    ------
    AnnData
    """
    idx = [i for i, g in enumerate(genes) if g in adata.var_names]
    print('There are {} gene in selected genes'.format(len(idx)))
    new_X = scipy.sparse.csr_matrix((adata.shape[0], len(genes)))
    for i in range(new_X.shape[0]//chunk_size+1):
        new_X[i*chunk_size:(i+1)*chunk_size, idx] = adata[i*chunk_size:(i+1)*chunk_size, genes[idx]].X
    adata = AnnData(new_X, obs=adata.obs, var={'var_names':genes}) 
    return adata


class BatchSampler(Sampler):
    """
    Batch-specific Sampler
    sampled data of each batch is from the same dataset.
    """
    def __init__(self, batch_size, batch_id, drop_last=False):
        """
        create a BatchSampler object
        
        Parameters
        ----------
        batch_size
            batch size for each sampling
        batch_id
            batch id of all samples
        drop_last
            drop the last samples that not up to one batch
            
        """
        self.batch_size = batch_size
        self.drop_last = drop_last
        self.batch_id = batch_id

    def __iter__(self):
        batch = {}
        sampler = np.random.permutation(len(self.batch_id))
        for idx in sampler:
            c = self.batch_id[idx]
            if c not in batch:
                batch[c] = []
            batch[c].append(idx)

            if len(batch[c]) == self.batch_size:
                yield batch[c]
                batch[c] = []

        for c in batch.keys():
            if len(batch[c]) > 0 and not self.drop_last:
                yield batch[c]
            
    def __len__(self):
        if self.drop_last:
            return len(self.batch_id) // self.batch_size
        else:
            return (len(self.batch_id)+self.batch_size-1) // self.batch_size
        
    
class SingleCellDataset(Dataset):
    """
    Dataloader of single-cell data
    """
    def __init__(self, adata):
        """
        create a SingleCellDataset object
            
        Parameters
        ----------
        adata
            AnnData object wrapping the single-cell data matrix
        """
        self.adata = adata
        self.shape = adata.shape
        
    def __len__(self):
        return self.adata.X.shape[0]
    
    def __getitem__(self, idx):
        x = self.adata.X[idx].toarray().squeeze()
        domain_id = self.adata.obs['batch'].cat.codes[idx]
        return x, domain_id, idx


def load_data(
        data_list, 
        batch_categories=None, 
        profile='RNA',
        join='inner', 
        batch_key='batch', 
        batch_name='batch',
        min_features=600, 
        min_cells=3, 
        n_top_features=2000, 
        batch_size=64, 
        chunk_size=CHUNK_SIZE,
        log=None,
        processed=False
    ):
    """
    Load dataset with preprocessing
    
    Parameters
    ----------
    data_list
        A path list of AnnData matrices to concatenate with. Each matrix is referred to as a 'batch'.
    batch_categories
        Categories for the batch annotation. By default, use increasing numbers.
    join
        Use intersection ('inner') or union ('outer') of variables of different batches. Default: 'inner'.
    batch_key
        Add the batch annotation to obs using this key. Default: 'batch'.
    batch_name
        Use this annotation in obs as batches for training model. Default: 'batch'.
    min_features
        Filtered out cells that are detected in less than min_features. Default: 600.
    min_cells
        Filtered out genes that are detected in less than min_cells. Default: 3.
    n_top_features
        Number of highly-variable genes to keep. Default: 2000.
    batch_size
        Number of samples per batch to load. Default: 64.
    chunk_size
        Number of samples from the same batch to transform. Default: 20000.
    log
        If log, record each operation in the log file. Default: None.
    
    Returns
    -------
    adata
        The AnnData object after combination and preprocessing.
    trainloader
        An iterable over the given dataset for training.
    testloader
        An iterable over the given dataset for testing
    """
    adata = concat_data(data_list, batch_categories, join=join, batch_key=batch_key)
    if log: log.info('Raw dataset shape: {}'.format(adata.shape))
    if batch_name!='batch':
        adata.obs['batch'] = adata.obs[batch_name]
    if 'batch' not in adata.obs:
        adata.obs['batch'] = 'batch'
    adata.obs['batch'] = adata.obs['batch'].astype('category')
    
    if not processed: 
        adata = preprocessing(
            adata, 
            profile=profile,
            min_features=min_features, 
            min_cells=min_cells, 
            n_top_features=n_top_features,
            chunk_size=chunk_size,
            log=log)
    else:
        adata=batch_scale(adata, chunk_size=chunk_size)
        
    scdata = SingleCellDataset(adata) # Wrap AnnData into Pytorch Dataset
    trainloader = DataLoader(
        scdata, 
        batch_size=batch_size, 
        drop_last=True, 
        shuffle=True, 
        num_workers=4
    )
    batch_sampler = BatchSampler(batch_size, adata.obs['batch'], drop_last=False)
    testloader = DataLoader(scdata, batch_sampler=batch_sampler)
    
    return adata, trainloader, testloader 