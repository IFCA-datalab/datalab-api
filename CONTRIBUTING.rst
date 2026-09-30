.. highlight:: shell

============
Contributing
============

Contributions are welcome, and they are greatly appreciated! Every little bit
helps, and credit will always be given.

You can contribute in many ways:

Types of Contributions
----------------------

Report Bugs
~~~~~~~~~~~

Report bugs at https://github.com/IFCA-datalab/datalab-api/issues.

If you are reporting a bug, please include:

* Your operating system name and version.
* Any details about your local setup that might be helpful in troubleshooting.
* Detailed steps to reproduce the bug.

Fix Bugs
~~~~~~~~

Look through the GitHub issues for bugs. Anything tagged with "bug" and "help
wanted" is open to whoever wants to implement it.

Implement Features
~~~~~~~~~~~~~~~~~~

Look through the GitHub issues for features. Anything tagged with "enhancement"
and "help wanted" is open to whoever wants to implement it.

Write Documentation
~~~~~~~~~~~~~~~~~~~

DataLab API could always use more documentation, whether as part of the
official DataLab API docs, in docstrings, or even on the web in blog posts,
articles, and such.

Submit Feedback
~~~~~~~~~~~~~~~

The best way to send feedback is to file an issue at https://github.com/IFCA-datalab/datalab-api/issues.

If you are proposing a feature:

* Explain in detail how it would work.
* Keep the scope as narrow as possible, to make it easier to implement.
* Remember that this is a volunteer-driven project, and that contributions
  are welcome :)

Get Started!
------------

Ready to contribute? Here's how to set up the project for local development.

1. Fork the `datalab-api` repo on GitHub.
2. Clone your fork locally and install it with `uv <https://docs.astral.sh/uv/>`_::

    $ git clone git@github.com:your_name_here/datalab-api.git
    $ cd datalab-api/
    $ uv sync

3. Create a branch for local development::

    $ git checkout -b name-of-your-bugfix-or-feature

   Now you can make your changes locally.

4. When you're done making changes, check that they pass the linters, the type
   checker and the tests::

    $ uv run ruff format && uv run ruff check
    $ uv run mypy
    $ uv run pytest

5. Commit your changes and push your branch to GitHub::

    $ git add .
    $ git commit -m "Your detailed description of your changes."
    $ git push origin name-of-your-bugfix-or-feature

6. Submit a pull request through the GitHub website.

Never commit secrets (``.env``, tokens, passwords).

Pull Request Guidelines
-----------------------

Before you submit a pull request, check that it meets these guidelines:

1. The pull request should include tests.
2. If the pull request adds functionality, the docs should be updated and the
   feature added to the list in README.rst.
3. The pull request should work for Python 3.12 and 3.13. Check
   https://github.com/IFCA-datalab/datalab-api/actions
   and make sure that the checks pass.

Tips
----

To run a subset of tests::

    $ uv run pytest tests/test_deployments.py

Deploying
---------

A reminder for the maintainers on how to deploy. Update the version in
``pyproject.toml``, commit it, then tag and push::

    $ git tag v0.2.0
    $ git push --tags

The "Release" workflow publishes the package to PyPI and the "Docker image"
workflow publishes the container image.

Code of Conduct
---------------

Please note that this project is released with a `Contributor Code of Conduct`_.
By participating in this project you agree to abide by its terms.

.. _`Contributor Code of Conduct`: CODE_OF_CONDUCT.rst
