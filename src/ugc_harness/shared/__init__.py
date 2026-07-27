"""Infrastructure shared by all pipeline stages.

Import concrete shared modules directly so package initialization does not
eagerly load every stage and create circular dependencies.
"""
