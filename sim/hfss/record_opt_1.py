# ----------------------------------------------
# Script Recorded by Ansys Electronics Desktop Version 2026.1.2
# 20:41:34  Aug 12, 2026
# ----------------------------------------------
import ScriptEnv
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")
oDesktop.RestoreWindow()
oProject = oDesktop.SetActiveProject("SMA_Coupon_GCPW_Opt")
oDesign = oProject.SetActiveDesign("SMA_Coupon_GCPW_Opt_4L")
oModule = oDesign.GetModule("Optimetrics")
oModule.InsertSetup("OptiParametric", 
	[
		"NAME:ParametricSetup1",
		"IsEnabled:="		, True,
		[
			"NAME:ProdOptiSetupDataV2",
			"SaveFields:="		, False,
			"CopyMesh:="		, False,
			"SolveWithCopiedMeshOnly:=", True
		],
		[
			"NAME:StartingPoint"
		],
		"Sim. Setups:="		, ["HFSS_Adaptive"],
		[
			"NAME:Sweeps",
			[
				"NAME:SweepDefinition",
				"Variable:="		, "comp_induct_w",
				"Data:="		, "LIN 0.125mm 0.375mm 0.05mm",
				"OffsetF1:="		, False,
				"Synchronize:="		, 0
			],
			[
				"NAME:SweepDefinition",
				"Variable:="		, "comp_induct_len",
				"Data:="		, "LIN 0.125mm 0.375mm 0.05mm",
				"OffsetF1:="		, False,
				"Synchronize:="		, 0
			],
			[
				"NAME:SweepDefinition",
				"Variable:="		, "comp_cap_w",
				"Data:="		, "LIN 0.125mm 0.375mm 0.05mm",
				"OffsetF1:="		, False,
				"Synchronize:="		, 0
			],
			[
				"NAME:SweepDefinition",
				"Variable:="		, "comp_cap_len",
				"Data:="		, "LIN 0.125mm 0.375mm 0.05mm",
				"OffsetF1:="		, False,
				"Synchronize:="		, 0
			],
			[
				"NAME:SweepDefinition",
				"Variable:="		, "void_l2_x",
				"Data:="		, "LIN 0.125mm 0.375mm 0.05mm",
				"OffsetF1:="		, False,
				"Synchronize:="		, 0
			],
			[
				"NAME:SweepDefinition",
				"Variable:="		, "void_l2_y",
				"Data:="		, "LIN 0.125mm 0.375mm 0.05mm",
				"OffsetF1:="		, False,
				"Synchronize:="		, 0
			],
			[
				"NAME:SweepDefinition",
				"Variable:="		, "void_l3_x",
				"Data:="		, "LIN 0.125mm 0.375mm 0.05mm",
				"OffsetF1:="		, False,
				"Synchronize:="		, 0
			],
			[
				"NAME:SweepDefinition",
				"Variable:="		, "void_l3_y",
				"Data:="		, "LIN 0.125mm 0.375mm 0.05mm",
				"OffsetF1:="		, False,
				"Synchronize:="		, 0
			]
		],
		[
			"NAME:Sweep Operations"
		],
		[
			"NAME:Goals"
		]
	])
oProject.Save()
oModule.ValidateSetup("ParametricSetup1")
