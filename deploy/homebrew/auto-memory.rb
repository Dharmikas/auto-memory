class AutoMemory < Formula
  include Language::Python::Virtualenv

  desc "Session memory recall for AI coding agents"
  homepage "https://github.com/dezgit2025/auto-memory"
  url "https://github.com/dezgit2025/auto-memory.git",
      tag: "v0.4.1",
      revision: "1be648725714e28c9ce3dbc55f8bd4bef64b4d90"
  license "MIT"

  depends_on "python@3"

  def install
    virtualenv_create(libexec, "python3")
    system libexec/"bin/pip", "install", *std_pip_args(prefix: libexec), "."
    bin.install_symlink libexec/"bin/session-recall"
  end

  test do
    assert_match "usage", shell_output("#{bin}/session-recall --help", 2)
  end
end
